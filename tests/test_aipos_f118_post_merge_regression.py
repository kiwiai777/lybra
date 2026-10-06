"""AIPOS-F118(gap #77) 靶场: finalize 合并后回归检查——两卡各自 PASS、合并后组合红。

靶场(临时治理根 + 临时产品仓, 禁真门/真治理根/真工位; 无 .deploy 与部署脚本 = 部署不适用, 不 push):
  产品仓 main: calc.rate() = 2; tests/test_base.py 断言 2; tests/test_legacy.py 存量红(合并前后都红 = 不算新增)。
  卡 A: rate() 改为 3 并同步改 test_base —— 在自己分支上只有存量红(各自 PASS)。
  卡 B(基于旧 main): 新增 tests/test_b.py 断言 rate()*10 == 20 —— 在自己分支上只有存量红(各自 PASS)。
  先 finalize A(无新红: 只一行结果, 零噪音), 再 finalize B → 合并结果上 test_b 红 = 组合失败。
  测试清单 = 真 run-all 执行器(tools/aipos_cli/runall_discovery, `# lybra-runall: discover`), 失败集合口径与 run-all 基线 grep 同一。

件①: warn 记入 finalization 记录 + 一行输出, 合并照常; block 撤销合并(main 复位)、不写 finalization 记录、落回归事件记录、给出口。
      B 的合并前基线取 A 的 finalization 记录(merge_commit = B 合并提交第一父)—— 不再跑第二遍。
件②: off 不跑但出一行; 未声明 runall_path 跳过并注明; async 拉起后台子进程立即继续, 结果落回归事件记录; block+async 形坏合并前拒。
      超时整组 SIGKILL, 按策略处置(不当作通过)。
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.aipos_cli import post_merge_regression as pmr
from tools.aipos_cli.finalize import finalize_task
from tools.aipos_cli.runall_discovery import failure_set

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
CARD_A, CARD_B = "AIPOS-T118A", "AIPOS-T118B"
ACTOR = "exec.test.f118"
RUNALL = "tests/run-all.sh"


@pytest.fixture(autouse=True)
def _isolated_tmp(tmp_path, monkeypatch):
    """检查的临时 worktree 与日志(含 async 子进程)落本用例 tmp_path, 不往系统 /tmp 留文件。"""
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert res.returncode == 0, f"git {args}: {res.stdout}{res.stderr}"
    return res.stdout.strip()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _runall_text() -> str:
    # 靶场清单 = 真 run-all 执行器(产品仓 tools/aipos_cli/runall_discovery), 按 discover 声明自动发现靶场产品仓的测试文件
    return (
        "#!/usr/bin/env bash\n"
        "set -u\n"
        'cd "$(dirname "$0")/.."\n'
        f'PYTHONPATH="{PRODUCT_ROOT}" python3 -m tools.aipos_cli.runall_discovery --runall tests/run-all.sh --repo-root "$(pwd)"\n'
        "exit $?\n"
        "# lybra-runall: discover\n"
    )


def _verdict(gov: Path, task_id: str, sha: str) -> None:
    vid = f"verdict_{task_id}_20261006_100000_auditor"
    _write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{vid}.md", (
        "---\nrecord_type: audit_verdict_record\n"
        f"verdict_id: {vid}\nverdict_at: '2026-10-06T10:00:00Z'\nreviewed_task_id: {task_id}\nverdict: PASS\n"
        f"artifact_subject:\n  commit_sha: {sha}\n  tree_hash: dummy\n---\n# PASS\n"))


def _branch(repo: Path, task_id: str, files: dict[str, str]) -> str:
    _git(repo, "checkout", "-q", "-b", f"card/{task_id}", "main")
    for rel, text in files.items():
        _write(repo / rel, text)
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"feat({task_id}): 卡改动")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return tip


def _rig(tmp_path: Path, policy: dict | None, *, runall: bool = True) -> dict:
    gov, repo = tmp_path / "gov", tmp_path / "repo"
    contract: dict = {"runall_path": RUNALL} if runall else {"require_tests": True}
    if policy is not None:
        contract["post_merge_regression"] = policy
    _write(gov / "project.json", json.dumps({"project_id": "t118", "test_contract": contract}))
    _write(gov / "card_policy.json", json.dumps({"schema_version": "1.0.0", "task_id_pattern": "AIPOS-[A-Z0-9-]+"}))
    _write(gov / "stage_archive" / "2026-10-06_stage.md", "# 靶场阶段快照\n")
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)  # 治理根标记: 读卡解析到本靶场根(不借真实 ~/.lybra 解析到真实治理根)
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t118")
    _git(repo, "config", "user.email", "t118@test.local")
    _write(repo / "calc.py", "def rate():\n    return 2\n")
    _write(repo / "tests" / "test_base.py", "import calc\n\n\ndef test_rate():\n    assert calc.rate() == 2\n")
    _write(repo / "tests" / "test_legacy.py", "def test_legacy_stale_red():\n    assert False, '存量红(合并前后都红)'\n")
    _write(repo / "conftest.py", "import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    _write(repo / RUNALL, _runall_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    tip_a = _branch(repo, CARD_A, {"calc.py": "def rate():\n    return 3\n",
                                   "tests/test_base.py": "import calc\n\n\ndef test_rate():\n    assert calc.rate() == 3\n"})
    tip_b = _branch(repo, CARD_B, {"tests/test_b.py": "import calc\n\n\ndef test_b_uses_rate():\n    assert calc.rate() * 10 == 20\n"})
    _verdict(gov, CARD_A, tip_a)
    _verdict(gov, CARD_B, tip_b)
    return {"gov": gov, "repo": repo, "tip_a": tip_a, "tip_b": tip_b}


def _finalize(r: dict, task_id: str) -> dict:
    res = finalize_task(task_id, ACTOR, r["repo"], governance_root=r["gov"], push=False, deploy=False)
    print(f"\n=== finalize {task_id}: verdict={res['verdict']} ===\nmessage: {res['message']}")
    for op in res["operations"]:
        if pmr.MARKER in op or op.startswith("  ↳"):
            print(f"  - {op}")
    return res


def _marker_lines(res: dict) -> list[str]:
    return [op for op in res["operations"] if op.startswith(pmr.MARKER)]


def _fin_record(gov: Path, task_id: str) -> dict:
    from tools.aipos_cli.frontmatter import require_frontmatter

    files = sorted((gov / "5_tasks" / "records" / "finalizations" / task_id).glob("finalization_*.md"))
    assert len(files) == 1, files
    meta, _ = require_frontmatter(files[0])
    return meta


def _events(gov: Path, task_id: str) -> list[Path]:
    d = gov / "5_tasks" / "records" / "events" / task_id
    return sorted(d.glob(f"{pmr.EVENT_TYPE}_*.md")) if d.is_dir() else []


def test_each_card_alone_only_has_stale_red(tmp_path):
    """前提: 两卡在各自分支上只有存量红(单卡审计视角各自 PASS), 组合失败只在合并后出现。"""
    r = _rig(tmp_path, {"mode": "warn"})
    for task_id in ("tip_a", "tip_b"):
        run = pmr.run_suite(r["repo"], r[task_id], RUNALL, 120)
        print(f"\n{task_id} {r[task_id][:8]}: status={run['status']} failures={run['failures']}")
        assert run["status"] == "ran" and run["failures"] == ["tests/test_legacy.py", "tests/test_legacy.py::test_legacy_stale_red"], run
    assert "detached" not in _git(r["repo"], "worktree", "list"), "临时 worktree 须已清理"


def test_warn_combined_red_recorded_merge_kept_and_baseline_from_record(tmp_path):
    r = _rig(tmp_path, None)  # 未声明策略 = schema 缺省 warn/sync
    res_a = _finalize(r, CARD_A)
    assert res_a["verdict"] == "PASS"
    assert len(_marker_lines(res_a)) == 1 and "无新增失败" in _marker_lines(res_a)[0], "无新红 = 只一行结果(零噪音)"
    rec_a = _fin_record(r["gov"], CARD_A)["post_merge_regression"]
    assert rec_a["status"] == "pass" and rec_a["action"] == "none" and rec_a["mode"] == "warn" and rec_a["execution"] == "sync"
    assert rec_a["baseline_source"].startswith("run:") and rec_a["merged_complete"] is True
    assert rec_a["new_failures"] == [] and "tests/test_legacy.py" in rec_a["merged_failures"], "存量红在基线里, 不算新增"
    merge_a = _git(r["repo"], "rev-parse", "HEAD")

    res_b = _finalize(r, CARD_B)
    assert res_b["verdict"] == "PASS", "warn: 合并照常"
    line = _marker_lines(res_b)
    assert len(line) == 1 and "新增失败 2 条" in line[0] and "tests/test_b.py" in line[0] and "策略 warn" in line[0], line
    rec_b = _fin_record(r["gov"], CARD_B)["post_merge_regression"]
    print(f"finalization 记录 {CARD_B} post_merge_regression = {json.dumps(rec_b, ensure_ascii=False, indent=1)}")
    assert rec_b["status"] == "new_failures" and rec_b["action"] == "warned"
    assert rec_b["new_failures"] == ["tests/test_b.py", "tests/test_b.py::test_b_uses_rate"]
    assert rec_b["pre_merge_commit"] == merge_a and rec_b["baseline_source"].startswith("record:5_tasks/records/finalizations/AIPOS-T118A/")
    assert "baseline_log" not in rec_b, "合并前基线取 A 的记录, 不再跑第二遍"
    head = _git(r["repo"], "rev-parse", "HEAD")
    assert head == rec_b["merge_commit"] and _git(r["repo"], "rev-parse", "HEAD^2") == r["tip_b"], "warn: 合并保留"


def test_block_combined_red_undoes_merge_no_record_event_and_exit(tmp_path):
    r = _rig(tmp_path, {"mode": "block", "timeout_seconds": 300})
    assert _finalize(r, CARD_A)["verdict"] == "PASS"
    merge_a = _git(r["repo"], "rev-parse", "HEAD")
    res_b = _finalize(r, CARD_B)
    assert res_b["verdict"] == "BLOCK" and res_b["category"] == "POST_MERGE_REGRESSION"
    assert res_b["pushed"] is False and res_b["deployed"] is False
    assert "策略 block: 已撤销合并, 未推送未部署" in res_b["message"] and "出口:" in res_b["message"] and f"card/{CARD_B}" in res_b["message"]
    assert _git(r["repo"], "rev-parse", "HEAD") == merge_a, "main 复位到合并前"
    assert _git(r["repo"], "status", "--porcelain") == "", "复位后工作树干净"
    assert _git(r["repo"], "rev-parse", f"card/{CARD_B}") == r["tip_b"], "卡分支保留"
    assert not (r["gov"] / "5_tasks" / "records" / "finalizations" / CARD_B).exists(), "合并已撤销 = 不写 finalization 记录"
    events = _events(r["gov"], CARD_B)
    assert len(events) == 1
    from tools.aipos_cli.frontmatter import require_frontmatter

    meta, body = require_frontmatter(events[0])
    print(f"回归事件记录 {events[0].name}:\n{body}")
    assert meta["event_type"] == pmr.EVENT_TYPE and meta["post_merge_regression"]["action"] == "blocked"
    assert meta["post_merge_regression"]["new_failures"] == ["tests/test_b.py", "tests/test_b.py::test_b_uses_rate"]


def test_off_and_undeclared_runall_emit_one_line_and_record(tmp_path):
    r = _rig(tmp_path / "off", {"mode": "off"})
    res = _finalize(r, CARD_A)
    assert res["verdict"] == "PASS" and len(_marker_lines(res)) == 1 and "已关闭" in _marker_lines(res)[0]
    assert _fin_record(r["gov"], CARD_A)["post_merge_regression"]["status"] == "off"
    r2 = _rig(tmp_path / "skip", None, runall=False)
    res2 = _finalize(r2, CARD_A)
    assert res2["verdict"] == "PASS" and "跳过(项目未声明 test_contract.runall_path" in _marker_lines(res2)[0]
    assert _fin_record(r2["gov"], CARD_A)["post_merge_regression"]["status"] == "skipped"


def test_async_returns_immediately_and_background_records_event(tmp_path):
    r = _rig(tmp_path, {"mode": "warn", "execution": "async", "timeout_seconds": 300})
    assert _finalize(r, CARD_A)["verdict"] == "PASS"
    pmr.ASYNC_CHILDREN.pop().wait(timeout=300)  # 同步等待本进程拉起的后台子进程
    res_b = _finalize(r, CARD_B)
    assert res_b["verdict"] == "PASS" and "异步执行中" in _marker_lines(res_b)[0]
    rec = _fin_record(r["gov"], CARD_B)["post_merge_regression"]
    assert rec["status"] == "async_started" and rec["async_pid"] > 0
    child = pmr.ASYNC_CHILDREN.pop()
    assert child.wait(timeout=300) == 0, Path(rec["async_log"]).read_text(encoding="utf-8")
    print(f"async 日志:\n{Path(rec['async_log']).read_text(encoding='utf-8')}")
    from tools.aipos_cli.frontmatter import require_frontmatter

    meta, _ = require_frontmatter(_events(r["gov"], CARD_B)[0])
    got = meta["post_merge_regression"]
    assert got["action"] == "recorded" and got["status"] == "new_failures" and "tests/test_b.py" in got["new_failures"]
    assert got["baseline_source"].startswith("record:5_tasks/records/events/AIPOS-T118A/"), "A 的异步结果作 B 的基线"


def test_block_with_async_rejected_before_merge(tmp_path):
    r = _rig(tmp_path, {"mode": "block", "execution": "async"})
    head = _git(r["repo"], "rev-parse", "HEAD")
    res = _finalize(r, CARD_A)
    assert res["verdict"] == "BLOCK" and "TEST_CONTRACT_INVALID" in res["message"] and "出口" in res["message"]
    assert _git(r["repo"], "rev-parse", "HEAD") == head, "合并前即拒, 不留合并"


@pytest.mark.parametrize("mode,expected", [("warn", "warned"), ("block", "blocked")])
def test_timeout_is_not_a_pass(tmp_path, mode, expected):
    r = _rig(tmp_path, {"mode": mode, "timeout_seconds": 3})
    _git(r["repo"], "checkout", "-q", f"card/{CARD_A}")
    _write(r["repo"] / "tests" / "test_slow.py", "import time\n\n\ndef test_slow():\n    time.sleep(60)\n")
    _git(r["repo"], "add", "tests/test_slow.py")
    _git(r["repo"], "commit", "-q", "-m", "slow")
    _verdict(r["gov"], CARD_A, _git(r["repo"], "rev-parse", "HEAD"))
    _git(r["repo"], "checkout", "-q", "main")
    res = _finalize(r, CARD_A)
    line = _marker_lines(res)[0]
    assert "检查未完成(timeout" in line and "不当作通过" in line, line
    if expected == "blocked":
        assert "timeout_seconds" in res["message"] and "出口" in res["message"]
    if expected == "warned":
        assert res["verdict"] == "PASS" and _fin_record(r["gov"], CARD_A)["post_merge_regression"]["action"] == "warned"
    else:
        assert res["verdict"] == "BLOCK" and res["post_merge_regression"]["action"] == "blocked"
    leftover = subprocess.run(["pgrep", "-f", "test_slow.py"], capture_output=True, text=True)
    assert leftover.stdout.strip() == "", f"超时须整组清理: {leftover.stdout}"


def test_failure_set_parsing_single_口径():
    out = "\n".join([
        "── tests/a.py ───", "FAILED tests/a.py::test_x - assert", "✗ tests/a.py FAIL",
        "── tests/b.py ───", "FAILED tests/b.py::test_known - assert", "[runall_discovery] 已知存量失败 1 条被容忍", "✓ tests/b.py PASS",
        "✗ 声明行失效: tests/run-all.sh 声明 `exclude gone.py`",
    ])
    assert failure_set(out, 1, RUNALL) == ["tests/a.py", "tests/a.py::test_x", "声明行失效: tests/run-all.sh 声明 `exclude gone.py`"]
    assert failure_set("boom\n", 2, "ci.sh") == ["ci.sh 退出码 2(未解析到 ✗ 行)"], "非本格式清单退出非 0 = 不当作通过"
    assert failure_set("✓ tests/a.py PASS\n", 0, RUNALL) == []


def test_loop_line_extraction_and_policy_validation(tmp_path):
    human = "\n=== Finalize Result ===\nOperations:\n  - Merged\n  - 合并后回归: 无新增失败(…)\n  -   ↳ 提示: x\n"
    assert pmr.result_line(human) == "合并后回归: 无新增失败(…)"
    assert pmr.result_line('{\n  "operations": [\n    "合并后回归: ✗ 新增失败 1 条 [t](策略 warn: 已记录, 合并照常)",\n') == \
        "合并后回归: ✗ 新增失败 1 条 [t](策略 warn: 已记录, 合并照常)"
    assert pmr.result_line("nothing\n") is None
    src = (PRODUCT_ROOT / "tools" / "aipos_cli" / "loop_driver.py").read_text(encoding="utf-8")
    assert "result_line(step.output)" in src, "loop finalize 步输出回归结果一行"
    from tools.aipos_cli.workspace_config import project_test_contract

    for bad, needle in (({"mode": "strict"}, "不在声明值域"), ({"timeout_seconds": 0}, "正整数"), ({"x": 1}, "未声明键"), ("warn", "须为对象")):
        gov = tmp_path / f"g{abs(hash(str(bad)))}"
        _write(gov / "project.json", json.dumps({"test_contract": {"runall_path": RUNALL, "post_merge_regression": bad}}))
        with pytest.raises(ValueError, match="TEST_CONTRACT_INVALID") as exc:
            project_test_contract(gov)
        assert needle in str(exc.value)
    gov = tmp_path / "default"
    _write(gov / "project.json", json.dumps({"test_contract": {"runall_path": RUNALL}}))
    got = project_test_contract(gov)["post_merge_regression"]
    assert (got["mode"], got["execution"], got["timeout_seconds"]) == ("warn", "sync", 420), "缺省 warn/sync/420(声明 config.schema)"


def test_recorded_baseline_lookup_ignores_legacy_and_voices_unreadable(tmp_path):
    """基线查找: 无回归字段的存量记录不是候选(不出声); 带回归字段却读不出的记录不作基线且进 notes; 合并后集合不完整的记录不作基线。"""
    gov = tmp_path / "gov"
    fin = gov / "5_tasks" / "records" / "finalizations"
    _write(fin / "AIPOS-OLD" / "finalization_AIPOS-OLD_1.md", "---\nrecord_type: finalization\nmerge_commit: abc\n---\n# 存量\n")
    _write(fin / "AIPOS-BAD" / "finalization_AIPOS-BAD_1.md", "---\npost_merge_regression: {merge_commit: abc\n---\n")
    _write(fin / "AIPOS-INC" / "finalization_AIPOS-INC_1.md",
           "---\npost_merge_regression:\n  merge_commit: abc\n  merged_complete: false\n---\n")
    got = pmr.lookup_recorded_baseline(gov, "abc")
    assert got["failures"] is None and len(got["unreadable"]) == 1 and "AIPOS-BAD" in got["unreadable"][0], got
    _write(fin / "AIPOS-OK" / "finalization_AIPOS-OK_1.md",
           "---\npost_merge_regression:\n  merge_commit: abc\n  merged_complete: true\n  merged_failures:\n  - tests/x.py\n---\n")
    got = pmr.lookup_recorded_baseline(gov, "abc")
    assert got["failures"] == ["tests/x.py"] and got["ref"].endswith("AIPOS-OK/finalization_AIPOS-OK_1.md")


def test_post_merge_modules_in_f120_preload_closure():
    """AIPOS-F120 导入闸: 合并后本进程禁从磁盘加载未预载的产品模块——合并后回归用到的模块须在预载闭包内(静态扫源码得出)。
    上面各 finalize 靶场在闸已装的情况下走完合并后回归(含 block 撤销合并 / async 拉起 / 事件记录), 即闭合的运行证据。"""
    from tools.aipos_cli.finalize import _post_merge_import_closure

    closure = set(_post_merge_import_closure())
    for name in ("tools.aipos_cli.post_merge_regression", "tools.aipos_cli.runall_discovery", "tools.aipos_cli.workspace_config",
                 "tools.aipos_cli.record_writer", "tools.aipos_cli.frontmatter", "tools.aipos_cli.clock", "tools.schema_constants"):
        assert name in closure, name
