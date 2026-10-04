"""AIPOS-F111 — 并行审计: 自动拉起按卡号取开工提示 + tests/run-all.sh 并集合并免冲突。

靶场 = AIPOS-F95 同一靶场(F90 tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门; tmp 工位; 假 harness tests/fake_harness.py;
禁真门/真治理根/真工位/真 pi)。

验收(卡面 ★验收):
 ① 同一审计工位认领两张审计卡: 两张同时在办时 loop 各自拉起, 假 harness 收到的 kickoff 含各自卡号(逐字节 = 该卡
    `my-tasks --workstation <dir> --task-id <卡>` 的 next_card.kickoff); 两张同时拉起各自进程组、各自汇总前缀;
    已结案 / 非本实例认领 → 拒拉起退回手工, 拒因原样转述(判据 next_resolver.kickoff_refusal 唯一实现)
 ② 仓根 .gitattributes 为 tests/run-all.sh 声明 merge=union: 临时仓两分支各在末尾追加登记块 → git merge 无冲突,
    合并后 bash -n 通过、两块均在; 对照组(无该声明)同一操作冲突
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源(禁第二份)
import test_aipos_f95_harness_launch as f95  # noqa: E402  — 拉起靶场唯一来源
from test_aipos_f78_engine_agnostic import DRIVER, EXEC  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from tools.aipos_cli import loop_driver  # noqa: E402
from tools.aipos_cli.autonomy_policy import load_policy  # noqa: E402
from tools.aipos_cli.harness_launch import LaunchedHarness  # noqa: E402
from tools.aipos_cli.loop_driver import plan_launch, run_loop, workstation_kickoff  # noqa: E402

TA, TB = "PROBE-F111-A", "PROBE-F111-B"
TAR, TBR = f"{TA}R", f"{TB}R"
AUDITOR = f95.AUDIT_CLAIMER


def _show(line: str) -> None:
    print(line, flush=True)


def _group_gone(pgid: int) -> bool:
    """SIGKILL 后组内孙进程(已改挂 init)收尸是异步的: 有界等待(≤5s)组内无进程。"""
    import time

    for _ in range(100):
        if not f95._group_alive(pgid):
            return True
        time.sleep(0.05)
    return False


def _ws_my_tasks(ws: Path, ref: str | None = None) -> dict:
    from tools.aipos_cli.aipos_cli import main

    buf = io.StringIO()
    argv = ["my-tasks", "--workstation", str(ws), "--json"] + (["--task-id", ref] if ref else [])
    with contextlib.redirect_stdout(buf):
        assert main(argv) == 0
    return json.loads(buf.getvalue())


def _loop(r, card: str) -> tuple[object, str]:
    out = io.StringIO()
    res = run_loop(card, r.gov, actor=DRIVER, policy_id=f95.POLICY_LAUNCH, out=out, interval=0.05, max_wait=60, max_steps=30)
    return res, out.getvalue()


def _launches(res) -> list[dict]:
    return [s.launch for s in res.steps if s.launch and s.launch.get("launched")]


# ===========================================================================
# ① 同一审计工位同时在办两张审计卡: 按卡号各取各的 kickoff
# ===========================================================================

def test_item1_same_audit_workstation_two_cards_each_launch_own_kickoff(lrig, monkeypatch):
    for card in (TA, TB):
        f90._card(lrig.gov, card, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    f95._land(lrig.gov, EXEC, "executor", lrig.ws_exec)
    f95._land(lrig.gov, AUDITOR, "auditor", lrig.ws_audit)

    # 阶段 1: 两卡各经 loop 走到「审计卡已认领、审计体在办」; 审计体早退 → 两张审计卡同时在办(同一审计实例/同一工位)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "early")
    for card in (TA, TB):
        res, text = _loop(lrig, card)
        _show(f"---- ① 阶段1 lybra loop {card} 输出原文 ----\n{text}")
        assert res.exit_code == 3 and [l["card"] for l in _launches(res)] == [card, f"{card}R"], (res.exit_code, text)
    for audit in (TAR, TBR):
        assert (lrig.gov / "5_tasks" / "queue" / "claimed" / f"{audit.lower()}.md").is_file(), audit
    # F111 前判据的反证: 该工位 next_card 只能是其中一张(NEXT_CARD_RULE 选出), 另一张按「next_card 必须等于目标卡」会被拒拉起
    bare = _ws_my_tasks(lrig.ws_audit)
    picked = bare["next_card"]["task_id"]
    skipped = {TAR: TBR, TBR: TAR}[picked]
    _show(f"[①] 审计工位 my-tasks(不带卡号) next_card={picked} "
          f"claimed={[t['task_id'] for t in bare['tasks'] if t.get('queue_state') == 'claimed']} → F111 前 {skipped} 只能退回手工")
    assert picked in (TAR, TBR)

    # 阶段 2: 同工位同时拉起两张 —— 各自进程组、各自汇总前缀, kickoff 各含各卡号, 逐字节 = my-tasks --task-id <卡>
    monkeypatch.setenv("FAKE_AUDIT_MODE", "hang")
    decl = loop_driver.load_loop_contract()["launch"]
    lines: list[str] = []
    plans = {c: plan_launch(lrig.gov, c, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                            decl=decl, already_launched=set()) for c in (TAR, TBR)}
    for c, p in plans.items():
        assert not p.refusal and p.cwd == str(lrig.ws_audit), (c, p.refusal)
        assert p.kickoff == _ws_my_tasks(lrig.ws_audit, c)["next_card"]["kickoff"]
    running = {c: LaunchedHarness(p, decl, lines.append) for c, p in plans.items()}
    try:
        for _ in range(100):  # 有界: 等两进程各自写下收到的 kickoff(假 harness 启动即写)
            for h in running.values():
                h.pump(0.05)
            if all((lrig.log / f"{c}.pgid").is_file() and (lrig.log / f"{c}.pgid").read_text() for c in running):
                break
        pgids = {c: h.pgid for c, h in running.items()}
        alive = {c: f95._group_alive(g) for c, g in pgids.items()}
        _show(f"[①] 同时拉起: pgid={pgids} 同时存活={alive}")
        assert pgids[TAR] != pgids[TBR] and all(alive.values())
        for c in running:
            got = (lrig.log / f"{c}.kickoff").read_bytes()
            assert got == plans[c].kickoff.encode("utf-8") and f"已认领任务卡 {c}。".encode() in got
            assert int((lrig.log / f"{c}.pgid").read_text()) == pgids[c]
            assert (lrig.log / f"{c}.cwd").read_text() == str(lrig.ws_audit)
    finally:
        for h in running.values():
            h.terminate_group(1)
            h.close()
    for c, h in running.items():
        lines.append(h.counts_line())
    text = "\n".join(lines)
    _show("---- ① 同时拉起两张 汇总原文 ----\n" + text)
    for c, h in running.items():
        assert f"[pi {c} pid={h.pid}] 助手: 开工 {c}: 先读卡面" in text, text
        assert _group_gone(h.pgid), f"{c} 进程组 {h.pgid} 未清净"
    other = {TAR: TBR, TBR: TAR}
    for c in running:  # 互不串台: 本卡汇总行不含另一张卡的号
        own = [ln for ln in lines if f"[pi {c} " in ln]
        assert own and not any(other[c] in ln.split("]", 1)[1] for ln in own), own

    # 阶段 3: loop 两次拉起各取各卡 kickoff, 都走到 completed(先审非 next_card 的那张: F111 前这一步拒拉起)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "work")
    for card in (skipped[:-1], picked[:-1]):
        res, text = _loop(lrig, card)
        _show(f"---- ① 阶段3 lybra loop {card} 输出原文 ----\n{text}")
        assert res.exit_code == 0 and res.outcome == "completed", text
        launched = _launches(res)
        assert [l["card"] for l in launched] == [f"{card}R"] and launched[0]["workstation"] == str(lrig.ws_audit), launched
        assert f"kickoff = 工位 my-tasks --task-id {card}R next_card.kickoff" in text
        kickoff = (lrig.log / f"{card}R.kickoff").read_text(encoding="utf-8")
        _show(f"[①] 假 harness 收到的 {card}R kickoff(原文):\n{kickoff}")
        assert f"已认领任务卡 {card}R。" in kickoff and f"task_cards/{card}R/" in kickoff
        assert other[f"{card}R"] not in kickoff
        assert (lrig.gov / "5_tasks" / "queue" / "completed" / f"{card.lower()}.md").is_file()

    # 拒因: 已结案 / 非本实例认领 → 不拉起, 拒因原样转述(kickoff_refusal 同一判据)
    done_kickoff, done_why = workstation_kickoff(lrig.gov, str(lrig.ws_audit), TAR)
    _show(f"[①拒因·已结案] {done_why}")
    assert done_kickoff == "" and f"{TAR} CONCLUDED: " in done_why, done_why
    mine_kickoff, mine_why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TAR)
    _show(f"[①拒因·执行工位指向已结案审计卡] {mine_why}")
    assert mine_kickoff == "" and f"{TAR} CONCLUDED: " in mine_why, mine_why  # 已结案优先于实例核验(判据顺序同 /go)
    plan = plan_launch(lrig.gov, TAR, policy=load_policy(lrig.gov, f95.POLICY_LAUNCH), no_launch=False,
                       decl=decl, already_launched=set())
    _show(f"[①拒因·plan_launch 已结案] refusal={plan.refusal!r} manual_hint={plan.manual_hint!r}")
    assert plan.refusal and "CONCLUDED" in plan.refusal and not plan.argv and "/go" in plan.manual_hint


def test_item1_refusal_relays_product_reason_not_mine_and_artifact_submitted(lrig):
    """单测拒因: 非本实例认领(NOT_MINE) / 产物已交(ARTIFACT_SUBMITTED) → workstation_kickoff 拒, 拒因含产品原文。"""
    from tools.aipos_cli.next_resolver import _ensure_worktree

    f90._card(lrig.gov, TA, "claimed", claimed_by=EXEC)
    f90.f78._claim_record(lrig.gov, TA, EXEC)
    assert _ensure_worktree(lrig.gov, TA)["ok"]
    f90._card(lrig.gov, TB, "claimed", assigned="exec.other.instance", claimed_by="exec.other.instance")
    f90.f78._claim_record(lrig.gov, TB, "exec.other.instance")
    ok, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TA)
    assert why == "" and f"已认领任务卡 {TA}。" in ok
    for card, code in ((TB, "NOT_MINE"), ("PROBE-F111-NOPE", "NOT_FOUND")):
        product = _ws_my_tasks(lrig.ws_exec, card)["next_card_excluded"][0]
        kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), card)
        _show(f"[①拒因·{code}] {why}")
        assert kickoff == "" and product["code"] == code and f"{card} {code}: {product['reason']}" in why, why
    # 产物已交 → ARTIFACT_SUBMITTED(防覆盖)
    _code_repo, worktree = f90.nr.card_worktree_location(lrig.gov, TA)
    f90._write(worktree / "tests" / "t.py", "x\n")
    f90._git(worktree, "add", "tests/t.py")
    f90._git(worktree, "commit", "-q", "-m", "w")
    sha, tree = f90._git(worktree, "rev-parse", "HEAD"), f90._git(worktree, "rev-parse", "HEAD^{tree}")
    f90._write(lrig.gov / "task_cards" / TA / "RETURN.md", f90.f78._return_text(TA, sha, tree))
    kickoff, why = workstation_kickoff(lrig.gov, str(lrig.ws_exec), TA)
    _show(f"[①拒因·ARTIFACT_SUBMITTED] {why}")
    assert kickoff == "" and f"{TA} ARTIFACT_SUBMITTED: " in why


def test_item1_single_implementation_and_no_next_card_equality_restriction():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    body = src.split("def workstation_kickoff(", 1)[1].split("\ndef ", 1)[0]
    assert '"--task-id", card' in body and "≠ 等待目标卡" not in body
    assert "render_kickoff" not in body and "kickoff_refusal(" not in body  # 渲染/核验只在产品 my-tasks 一处, 此处不另判
    assert "except Exception" not in src and "except:" not in src


# ===========================================================================
# ② tests/run-all.sh merge=union: 两分支各追加登记块 → 合并无冲突, 两块均在, bash -n 通过
# ===========================================================================

_FOOTER = '\necho\necho "========================================================"\nif [ "$overall" -eq 0 ]; then'


def _block(name: str, shape: str) -> str:
    """登记块: one_line = 一行注释 + 一行 run_pytest(F111 起的登记式样); classic = F111 前多行 if/else/fi 块(展示 union 局限)。"""
    if shape == "one_line":
        return f"\n# {name}: 夹具登记\nrun_pytest \"tests/test_{name}.py\" \"$REPO_ROOT/tests/test_{name}.py\"\n"
    return (f"\n# {name}: 夹具登记块\necho\necho \"── tests/test_{name}.py ──\"\n"
            f"if PYTHONPATH=\"$REPO_ROOT\" python3 -m pytest \"$REPO_ROOT/tests/test_{name}.py\" -v --tb=short; then\n"
            f"  echo \"✓ tests/test_{name}.py PASS\"\nelse\n  echo \"✗ tests/test_{name}.py FAIL\"\n  overall=1\nfi\n")


def _g(repo: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=f111", "-c", "user.email=f111@fixture", *argv], cwd=repo,
                          capture_output=True, text=True, check=check)


def _merge_demo(tmp: Path, with_attributes: bool, shape: str = "one_line") -> tuple[subprocess.CompletedProcess, Path]:
    repo = tmp / f"{'union' if with_attributes else 'control'}-{shape}"
    (repo / "tests").mkdir(parents=True)
    _g(repo, "init", "-q", "-b", "main")
    shutil.copy(REPO_ROOT / "tests" / "run-all.sh", repo / "tests" / "run-all.sh")
    if with_attributes:
        shutil.copy(REPO_ROOT / ".gitattributes", repo / ".gitattributes")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "base")
    runall = repo / "tests" / "run-all.sh"
    base = runall.read_text(encoding="utf-8")
    assert base.count(_FOOTER) == 1, "run-all.sh 尾部汇总段形状变了, 夹具插入点需同步"
    for branch, name in (("card/X", "aipos_fx_alpha"), ("card/Y", "aipos_fy_beta")):
        _g(repo, "checkout", "-q", "-b", branch, "main")
        runall.write_text(base.replace(_FOOTER, _block(name, shape) + _FOOTER), encoding="utf-8")
        _g(repo, "commit", "-q", "-am", f"{branch} 登记夹具")
    _g(repo, "checkout", "-q", "main")
    _g(repo, "merge", "-q", "--no-ff", "-m", "Merge card/X", "card/X")
    return _g(repo, "merge", "--no-ff", "-m", "Merge card/Y", "card/Y", check=False), repo


def test_item2_runall_union_merge_two_appended_blocks_no_conflict(tmp_path):
    attr = _g(REPO_ROOT, "check-attr", "merge", "--", "tests/run-all.sh")
    _show(f"[②] 本仓 git check-attr: {attr.stdout.strip()}")
    assert attr.stdout.strip() == "tests/run-all.sh: merge: union"

    merged, repo = _merge_demo(tmp_path, with_attributes=True)
    text = (repo / "tests" / "run-all.sh").read_text(encoding="utf-8")
    syntax = subprocess.run(["bash", "-n", str(repo / "tests" / "run-all.sh")], capture_output=True, text=True)
    status = _g(repo, "status", "--porcelain").stdout.strip()
    _show(f"[②] union 靶场 git merge card/Y: rc={merged.returncode}\n{merged.stdout.strip()}\n{merged.stderr.strip()}\n"
          f"git status --porcelain={status!r}; bash -n rc={syntax.returncode} {syntax.stderr.strip()!r}")
    _show("[②] 合并后尾部原文:\n" + text[text.index("# aipos_fx_alpha"):])
    assert merged.returncode == 0 and status == "" and syntax.returncode == 0
    assert "<<<<<<<" not in text and "=======\n" not in text
    for name in ("aipos_fx_alpha", "aipos_fy_beta"):
        assert text.count(f'run_pytest "tests/test_{name}.py" "$REPO_ROOT/tests/test_{name}.py"') == 1, name
        assert text.count(f"# {name}: 夹具登记\n") == 1, name
    assert text.index("aipos_fx_alpha") < text.index("aipos_fy_beta") < text.index(_FOOTER)
    assert text.count(_FOOTER) == 1 and text.count("\nrun_pytest() {\n") == 1
    # 合并结果真能跑: 以假 python3 替身执行合并后的登记段, 两块都被调用(各一次)、汇总照常
    calls = _run_registered_tail(repo, tmp_path)
    _show(f"[②] 合并后两块被 run_pytest 调用: {calls}")
    assert calls == ["tests/test_aipos_fx_alpha.py", "tests/test_aipos_fy_beta.py"], calls

    control, crepo = _merge_demo(tmp_path, with_attributes=False)
    _show(f"[②] 对照组(无 .gitattributes) git merge card/Y: rc={control.returncode}\n{control.stdout.strip()}")
    assert control.returncode != 0 and "CONFLICT" in control.stdout
    _g(crepo, "merge", "--abort")


def test_item2_limit_classic_multiline_blocks_union_eats_shared_tail_bash_n_catches(tmp_path):
    """局限(写进 .gitattributes 与 run_pytest 注释): F111 前的多行 if/else/fi 块两侧尾行相同(overall=1 / fi),
    union 只留一份 → 语法坏; 由 bash -n 兜底(本夹具 test_runall_syntax_ok 每次 run-all 都跑)。"""
    merged, repo = _merge_demo(tmp_path, with_attributes=True, shape="classic")
    syntax = subprocess.run(["bash", "-n", str(repo / "tests" / "run-all.sh")], capture_output=True, text=True)
    _show(f"[②局限] 多行块 union 合并 rc={merged.returncode}; bash -n rc={syntax.returncode}: {syntax.stderr.strip()}")
    assert merged.returncode == 0 and syntax.returncode != 0


def test_runall_syntax_ok_union_guard():
    """union 合并的兜底: 本仓 tests/run-all.sh 语法检查通过(合并出坏块即此处红)。"""
    syntax = subprocess.run(["bash", "-n", str(REPO_ROOT / "tests" / "run-all.sh")], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


def _run_registered_tail(repo: Path, tmp: Path) -> list[str]:
    """只执行合并后 run-all.sh 的 run_pytest 定义 + 两块登记 + 汇总段(python3 换成记录参数的替身), 返回被登记的标签序列。"""
    text = (repo / "tests" / "run-all.sh").read_text(encoding="utf-8")
    func = text[text.index("run_pytest() {"):text.index("\n}\n", text.index("run_pytest() {")) + 3]
    tail = text[text.index("# aipos_fx_alpha: 夹具登记"):]
    fake_bin = tmp / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "python3").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "python3").chmod(0o755)
    script = tmp / "tail.sh"
    script.write_text(f"set -u\nREPO_ROOT={repo}\noverall=0\n{func}{tail}", encoding="utf-8")
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env={"PATH": f"{fake_bin}:/usr/bin:/bin"})
    assert proc.returncode == 0 and "ALL TEST FILES PASS" in proc.stdout, proc.stdout + proc.stderr
    return [ln[2:-5] for ln in proc.stdout.splitlines() if ln.startswith("✓ ") and ln.endswith(" PASS")]


def test_item2_gitattributes_declares_only_runall_union_and_documents_limits():
    text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    rules = [ln.split() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert rules == [["tests/run-all.sh", "merge=union"]], rules
    assert "AIPOS-F111" in text and "重复行" in text and "F109" in text


def test_f111_fixture_registered_in_runall():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert 'run_pytest "tests/test_aipos_f111_parallel_audit.py" "$REPO_ROOT/tests/test_aipos_f111_parallel_audit.py"' in runall
