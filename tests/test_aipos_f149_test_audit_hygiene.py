"""AIPOS-F149 — 测试与审计卫生(gap #100 / #119 / #89)。

靶场全部临时目录(临时治理根 + 临时产品仓); 不碰真实治理根、真实工位与生产门。

 ① 测试不写产品仓检出目录(gap #100): tools/test_n0_validation.py 草稿改落隔离 HOME 下的临时根(HOME 隔离唯一实现
    runall_discovery.isolate_test_session); 不变量: 测试文件不得以「产品仓根 / 5_tasks」形写检出目录; 跑该文件(pytest 与脚本直跑)
    前后检出目录 git status(含 ignored)不变。
 ② 夹具进程清理只认本用例(gap #119): test_aipos_f118 超时用例的残留判据改按本用例专属标记(runall_discovery.marked_processes
    唯一实现), 并行 run-all 里他方同名进程不再误判; 不变量: 同名诱饵进程在跑时该用例照绿、诱饵不被碰; 测试文件里按命令行全机
    匹配进程(pgrep/pkill/killall)的用法只许棘轮基线内的存量(只减不增)。
 ③ 审计 main 侧基线复用(gap #89): 只读命令 `lybra regression baseline` 按 sha 取已记录的合并后回归失败集合(判据 =
    post_merge_regression.lookup_recorded_baseline, 与 finalize 取记录基线同一实现); 有完整记录 = recorded, 无记录 / 超时 /
    异步未落结果 = self_run_required; 章程(agents/roles/auditor/AGENTS.md 唯一文本源)与审计卡取证锚点给出该命令(同一渲染函数),
    被审分支一侧仍必须自跑。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import read_entries  # noqa: E402
from test_aipos_f99_audit_runall_baseline import AUDIT_SKILL, AUDITOR_MASTER, _auditor_ctx, _baseline_section, _make_gov  # noqa: E402
from test_aipos_f118_post_merge_regression import (  # noqa: E402  — 靶场唯一来源(禁第二份)
    CARD_A,
    _finalize,
    _fin_record,
    _git,
    _isolated_tmp,  # noqa: F401  — autouse: 检查的临时 worktree 与日志落本用例 tmp_path
    _rig,
    _write,
)
from tools.aipos_cli import post_merge_regression as pmr  # noqa: E402
from tools.aipos_cli.charter_render import render_charter  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

THIS = "tests/test_aipos_f149_test_audit_hygiene.py"
PROCESS_MATCH_BASELINE = REPO_ROOT / "tests" / "f149_process_match_baseline.jsonl"


def _show(text: str) -> None:
    print(text, flush=True)


def _test_files() -> list[str]:
    """产品仓全部测试文件(与 run-all 自动发现同一判据 discover_test_files)。"""
    return discover_test_files(repo_files(REPO_ROOT), {**default_test_contract(), "runall_path": "tests/run-all.sh"})


def _checkout_status() -> list[str]:
    """检出目录 git status(含 ignored 与未跟踪全量; 字节码缓存除外——解释器副产物, 不是测试写入)。只读。"""
    out = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain=v1", "--ignored", "--untracked-files=all"],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return sorted(ln for ln in out.splitlines() if "__pycache__" not in ln and ".pytest_cache" not in ln)


# ===========================================================================
# ① 测试不写产品仓检出目录
# ===========================================================================
_CHECKOUT_WRITE_RE = re.compile(r"""\b[A-Z_]*(?:REPO|PRODUCT)_ROOT\s*/\s*["']5_tasks["']""")


def test_item1_no_test_file_targets_checkout_5_tasks():
    """不变量(防回潮): 任何测试文件都不得以「产品仓根 / "5_tasks"」形拼路径(= 往检出目录写治理产物)。"""
    hits = []
    for rel in _test_files():
        if rel == THIS:
            continue
        for lineno, line in enumerate((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if _CHECKOUT_WRITE_RE.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert hits == [], "测试往产品仓检出目录拼 5_tasks 路径(改用临时根, 见 tools/test_n0_validation.scratch_root):\n" + "\n".join(hits)


def test_item1_n0_validation_leaves_checkout_unchanged():
    """跑 tools/test_n0_validation.py(pytest 收集 + 脚本直跑两种入口)前后, 检出目录 git status(含 ignored)逐行不变。"""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    before = _checkout_status()
    ran = [
        subprocess.run([sys.executable, "-m", "pytest", "tools/test_n0_validation.py", "-q", "-p", "no:cacheprovider"],
                       cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300),
        subprocess.run([sys.executable, "tools/test_n0_validation.py"], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300),
    ]
    after = _checkout_status()
    for res in ran:
        assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    _show(f"[①] 检出目录 git status(含 ignored, 去字节码缓存)前 {len(before)} 行 / 后 {len(after)} 行; 新增 {sorted(set(after) - set(before))}")
    assert after == before


def test_item1_scratch_root_refuses_unisolated_home(tmp_path, monkeypatch):
    """HOME 未隔离(标记 ≠ HOME)= 拒写(fail-closed), 不落真实 HOME。"""
    import importlib

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    n0 = importlib.import_module("test_n0_validation")
    monkeypatch.setattr(n0, "_SCRATCH_ROOT", None)
    monkeypatch.setenv("HOME", str(tmp_path / "pretend-real-home"))
    with pytest.raises(AssertionError, match="HOME 未隔离"):
        n0.scratch_root()


# ===========================================================================
# ② 夹具进程清理只认本用例
# ===========================================================================
def test_item2_same_named_decoy_does_not_redden_f118_timeout_case(tmp_path):
    """诱饵: 本用例自起一个命令行含 test_slow.py 的进程(模拟并行 run-all 里他方同名用例)。f118 超时用例在其存活时照绿,
    且诱饵不被碰(夹具只清本用例标记的进程)。诱饵由本用例按自身 pid 收尾。"""
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)", "tests/test_slow.py"], start_new_session=True)
    try:
        probe = subprocess.run(["pgrep", "-f", "test_slow.py"], capture_output=True, text=True)
        assert str(decoy.pid) in probe.stdout.split(), "诱饵须能被旧判据(pgrep -f test_slow.py)看见, 否则演示无效"
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_aipos_f118_post_merge_regression.py", "-q", "-p", "no:cacheprovider",
             "-k", "timeout_is_not_a_pass"],
            cwd=REPO_ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=600)
        _show(f"[②] 诱饵 pid {decoy.pid} 存活期间 f118 超时用例: rc={res.returncode} {res.stdout.strip().splitlines()[-1:]}")
        assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
        assert decoy.poll() is None, "夹具不得清理非本用例自起的进程"
    finally:
        if decoy.poll() is None:
            os.kill(decoy.pid, signal.SIGKILL)  # 本用例自起的诱饵, 按精确 pid 收尾
        decoy.wait(timeout=10)


_PROCESS_MATCH_RE = re.compile(r"""["'](pgrep|pkill|killall)["']""")


def _process_match_uses() -> list[dict[str, str]]:
    found = []
    for rel in _test_files():
        if rel == THIS:
            continue
        for line in (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines():
            if _PROCESS_MATCH_RE.search(line):
                found.append({"file": rel, "text": line.strip()})
    return found


def test_item2_process_match_ratchet():
    """棘轮(只减不增): 测试文件按命令行全机匹配进程(pgrep/pkill/killall 作 argv)的用法只许基线内存量(其模式含本仓/本用例
    独有路径); 新增 = 红(改用本用例标记 runall_discovery.marked_processes / 自起进程精确 pid/pgid); 存量已消失 = 红(删基线行)。"""
    baseline = read_entries(PROCESS_MATCH_BASELINE)
    found = _process_match_uses()
    key = lambda e: (e["file"], e["text"])  # noqa: E731
    new = sorted(set(map(key, found)) - set(map(key, baseline)))
    stale = sorted(set(map(key, baseline)) - set(map(key, found)))
    _show(f"[②] 棘轮: 基线 {len(baseline)} 条, 现存 {len(found)} 条, 新增 {len(new)}, 已消失 {len(stale)}")
    assert new == [], f"新增按命令行全机匹配进程的用法(并行 run-all 下会认到/误杀他方进程): {new}"
    assert stale == [], f"基线条目已不存在, 删 {PROCESS_MATCH_BASELINE.name} 对应行(只减不增): {stale}"
    assert all(e.get("reason") for e in baseline), "基线每条须写理由"
    assert not any("test_slow.py" in e["text"] for e in baseline), "f118 超时用例已改标记判据, 不得回潮"


# ===========================================================================
# ③ 审计 main 侧基线复用
# ===========================================================================
def _cli(args: list[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, timeout=120)


def _range(tmp_path: Path, project: str = "rangeproj") -> tuple[Path, Path, str]:
    """任意项目形靶场(非 lybra 项目名、任意路径): 治理根 + 产品仓(一个提交), 返回 (gov, repo, HEAD sha)。"""
    gov, repo = tmp_path / f"gov-{project}", tmp_path / f"repo-{project}"
    (gov / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.name", "f149"], ["config", "user.email", "f149@test.local"],
                 ["commit", "-q", "--allow-empty", "-m", "init"]):
        _git(repo, *args)
    _write(gov / "project.json", json.dumps({"project": project, "code_repo": str(repo), "config_version": 1}))
    return gov, repo, _git(repo, "rev-parse", "HEAD")


def _record(gov: Path, kind: str, task_id: str, name: str, pmr_field: dict) -> None:
    from tools.aipos_cli.record_writer import render_markdown

    meta = {"record_type": "finalization" if kind == "finalizations" else "task_progress_event", "post_merge_regression": pmr_field}
    _write(gov / "5_tasks" / "records" / kind / task_id / name, render_markdown(meta, "# rig\n", ["record_type", "post_merge_regression"]))


def test_item3_recorded_from_real_finalize_record(tmp_path):
    """端到端: 靶场 finalize(sync 合并后回归)落 finalization 记录 → 命令缺省查 main HEAD = 该合并提交 → recorded,
    失败集合与记录 merged_failures 逐条相同(同一实现读同一记录)。"""
    r = _rig(tmp_path, {"mode": "warn"})
    assert _finalize(r, CARD_A)["verdict"] == "PASS"
    rec = _fin_record(r["gov"], CARD_A)["post_merge_regression"]
    head = _git(r["repo"], "rev-parse", "HEAD")
    assert rec["merge_commit"] == head and rec["merged_complete"] is True
    res = _cli(["--workspace-root", str(r["gov"]), "regression", "baseline", "--repo-root", str(r["repo"])])
    _show("[③] 有记录 sha(缺省 = 产品仓 main HEAD)原文:\n" + res.stdout)
    assert res.returncode == 0, res.stderr
    assert f"sha {head}" in res.stdout and "结论: 已记录, 可复用" in res.stdout and "main 侧以本失败集合为基线, 不再自跑" in res.stdout
    assert "被审分支一侧仍须自跑" in res.stdout
    js = json.loads(_cli(["--workspace-root", str(r["gov"]), "regression", "baseline", "--repo-root", str(r["repo"]), "--json"]).stdout)
    assert js["status"] == pmr.AUDIT_BASELINE_RECORDED and js["failures"] == rec["merged_failures"]
    assert js["ref"].startswith(f"5_tasks/records/finalizations/{CARD_A}/") and js["commit"] == head
    assert js["failure_lines"] == ["tests/test_legacy.py"] and js["failure_nodes"] == ["tests/test_legacy.py::test_legacy_stale_red"]


def test_item3_no_record_and_incomplete_records_require_self_run(tmp_path):
    """无该 sha 记录 / 记录为超时 / 异步未落结果 = self_run_required(原因逐条列出); 后续落了完整记录 = recorded。"""
    gov, _repo, head = _range(tmp_path)
    res = _cli(["--workspace-root", str(gov), "regression", "baseline"])
    _show("[③] 无记录 sha 原文:\n" + res.stdout)
    assert res.returncode == 0 and "结论: 须自跑 main 侧" in res.stdout and f"无 merge_commit = {head}" in res.stdout
    assert "main 侧按审计章程建 main 基线副本自跑一次" in res.stdout
    _record(gov, "finalizations", "PRJ-1", "finalization_PRJ-1_1.md",
            {"merge_commit": head, "status": "async_started", "execution": "async", "merged_complete": False})
    _record(gov, "events", "PRJ-0", "post_merge_regression_PRJ-0_1.md",
            {"merge_commit": head, "status": "timeout", "error": "合并后一跑: 测试清单在 1500s 内未跑完", "merged_complete": False})
    js = json.loads(_cli(["--workspace-root", str(gov), "regression", "baseline", "--sha", head, "--json"]).stdout)
    _show("[③] 记录为超时/异步未落结果 原文(--json reasons):\n" + "\n".join(js["reasons"]))
    assert js["status"] == pmr.AUDIT_BASELINE_SELF_RUN and len(js["reasons"]) == 2
    assert any("status=timeout" in x for x in js["reasons"]) and any("status=async_started" in x for x in js["reasons"])
    _record(gov, "events", "PRJ-1", "post_merge_regression_PRJ-1_2.md",
            {"merge_commit": head, "status": "pass", "execution": "async", "merged_complete": True, "merged_failures": ["tests/x.py"]})
    js = json.loads(_cli(["--workspace-root", str(gov), "regression", "baseline", "--sha", head[:10], "--json"]).stdout)
    assert js["status"] == pmr.AUDIT_BASELINE_RECORDED and js["failures"] == ["tests/x.py"] and js["commit"] == head


def test_item3_unresolvable_sha_fails_closed_with_exit(tmp_path):
    gov, _repo, _head = _range(tmp_path)
    res = _cli(["--workspace-root", str(gov), "regression", "baseline", "--sha", "no-such-ref"])
    assert res.returncode == 1 and "解析不出提交" in res.stderr and "出口" in res.stderr
    (gov / "project.json").write_text(json.dumps({"project": "rangeproj", "config_version": 1}), encoding="utf-8")
    res = _cli(["--workspace-root", str(gov), "regression", "baseline"])
    assert res.returncode == 1 and "未声明产品仓" in res.stderr and "--repo-root" in res.stderr
    res = _cli(["--workspace-root", str(gov), "regression", "baseline", "--sha", "a" * 40])
    assert res.returncode == 0 and "须自跑 main 侧" in res.stdout, "完整 sha 原样用, 不需产品仓"


def test_item3_charter_renders_reuse_rule_with_declared_paths(tmp_path, monkeypatch):
    """章程渲染(lybra 形与他项目形): 含复用写法 + 按声明渲染的命令(治理根实值), 被审 tip 必须自跑; 无残留占位。"""
    for project, contract in (("lybra", {"runall_path": "tests/run-all.sh"}), ("otherproj", {"runall_path": "ci/all.sh"})):
        gov = _make_gov(tmp_path, monkeypatch, project=project, test_contract=contract)
        rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov, project))
        section = _baseline_section(rendered)
        if project == "lybra":
            _show("[③] 章程渲染(lybra 形)·独立全量基线节第 1 条:\n" + section.split("  2. ")[0])
        assert "{{" not in rendered
        assert f"`{pmr.audit_baseline_command(gov)}`" in section
        for needle in ("被审 tip 一侧**永远独立完整跑一次**", "**被审 tip 一侧必须自跑**", "main 一侧先复用已记录基线",
                       "**不手翻记录文件**", "**main 侧不再自跑**", "须自跑", "超时/未完成/读不出", "只跑被审 tip 一侧"):
            assert needle in section, needle
    skill = AUDIT_SKILL.read_text(encoding="utf-8")
    assert "已记录的合并后回归基线" in skill and "以章程为准" in skill and "regression baseline" not in skill, "技能只引用章程"


def test_item3_anchor_section_and_single_renderer(tmp_path, monkeypatch):
    """审计卡取证锚点给出本卡实值命令(治理根 + 被审卡产品仓), 与章程同一渲染函数; 命令字面量全产品只此一处。"""
    from tools.aipos_cli import audit_derivation as ad

    gov = _make_gov(tmp_path, monkeypatch, project="otherproj", test_contract={"runall_path": "ci/all.sh"})
    code_repo = json.loads((gov / "project.json").read_text(encoding="utf-8"))["code_repo"]
    section = ad.build_forensic_anchor_section("OTH-1", gov, {"task_id": "OTH-1", "project": "otherproj"}, audit_task_id="OTH-1R")
    _show("[③] 审计卡取证锚点:\n" + section)
    assert f"`{pmr.audit_baseline_command(gov, code_repo)}`" in section and "被审分支一侧必须自跑" in section
    assert "regression baseline" not in ad.build_forensic_anchor_section("OTH-1", None, None, audit_task_id="OTH-1R"), "治理根不可得不猜"
    hits = [str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "tools").rglob("*.py")
            if '"regression", "baseline"' in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == ["tools/aipos_cli/post_merge_regression.py"], hits
    for rel in ("tools/aipos_cli/charter_render.py", "tools/aipos_cli/audit_derivation.py"):
        assert "audit_baseline_command(" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
    src = (REPO_ROOT / "tools" / "aipos_cli" / "post_merge_regression.py").read_text(encoding="utf-8")
    assert src.count("def lookup_recorded_baseline(") == 1 and "lookup_recorded_baseline(governance_root, commit)" in src
    assert src.count("_recorded_regressions(governance_root)") == 1, "审计基线查询不另扫记录(复用 lookup_recorded_baseline)"
