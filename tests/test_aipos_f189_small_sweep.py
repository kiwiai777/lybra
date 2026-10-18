"""AIPOS-F189 — 小卡清扫③: 让两边基线对照可信的四处小毛病。

件① 到期时刻秒级抖动(gap #166): 向导信封 `--expires-at` / 授权建议 expires_at = 当前时刻 + 天数(秒精度), 同一测试多次渲染再逐字比较,
    跨秒边界即偶发不一致(F186 main 基线 F129、F187 main 基线 F180 各中一次)。修法 = 测试冻结被测代码既有的时钟入口(onboarding 模块引用的
    clock.utc_now / owner_inbox 调用时取的 clock.utc_now), 不另造时间源; 本文件以「入口逐次前进 1 秒」确定性复现根因, 并证冻结后一致。
件② kickoff 时限措辞(gap #191): 全量时限句按卡面 harness 的单次工具时限(enums.schema harness.<值>.tool_timeout_seconds)——
    时限内 = 同步跑写设足秒数; 超限 = 一律 run-long; 未声明 = 不写秒数只教 run-long。
件③ tools/lybra-deploy 不在主检出写 pyc: 脚本顶部统一 PYTHONDONTWRITEBYTECODE=1(本脚本及其所有 python3 子进程继承)。
件④ 退役 AIPOS-276 两条永远失败的测试(测的是 F185 已退役的 project-map 新鲜度告警)与 run-all known-failure 声明行。

靶场: 临时治理根 / 临时产品仓 / 临时 git 仓(禁真门 / 真治理根 / 真工位 / 真部署)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f187_kickoff_worktree_cleanup import (  # noqa: E402  — kickoff 靶场构件唯一来源
    CHRIS, CHRIS_EXEC, _cli, _land_subagent, _update_project,
)
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

KICKOFF = "lybra_kickoff"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


# ===========================================================================
# 件① 到期时刻取自时钟入口: 入口前进 1 秒 = 两次渲染不一致(根因复现); 冻结入口 = 一致
# ===========================================================================

GUIDE = dict(project_name="f189-probe", home_root="/tmp/f189-home", gate_url="http://127.0.0.1:7118", host_segment="devbox",
             workspace_dir="/tmp/f189-ws/exec", auditor_dir="/tmp/f189-ws/audit", owner_workspace="/tmp/f189-home/ops",
             code_repo="/tmp/f189-code/app", advisor_dir="/tmp/f189-session")


def _expires_args(guide: dict) -> list[str]:
    return re.findall(r"--expires-at (\S+)", json.dumps(guide["steps"], ensure_ascii=False))


def test_item1_guide_expiry_cross_second_reproduced_and_frozen(monkeypatch: pytest.MonkeyPatch):
    import tools.aipos_cli.onboarding as onboarding
    from tools.aipos_cli.clock import utc_now

    base = utc_now().replace(microsecond=999_999)
    ticks = iter([base, base + timedelta(microseconds=1)])  # 两次取值跨一个秒边界(真实抖动形)
    monkeypatch.setattr(onboarding, "utc_now", lambda: next(ticks))
    first, second = onboarding.generate_onboarding_guide(**GUIDE), onboarding.generate_onboarding_guide(**GUIDE)
    _show(f"[件①·跨秒两次渲染 --expires-at] {_expires_args(first)} vs {_expires_args(second)}")
    assert _expires_args(first) and _expires_args(first) != _expires_args(second), "根因: 到期秒取自时钟入口, 跨秒即不同"
    assert first["steps"] != second["steps"]

    monkeypatch.setattr(onboarding, "utc_now", lambda: base)  # 冻结同一入口(F129 夹具 frozen_guide_clock 同法)
    a, b = onboarding.generate_onboarding_guide(**GUIDE), onboarding.generate_onboarding_guide(**GUIDE)
    _show(f"[件①·冻结后两次渲染 --expires-at] {_expires_args(a)} vs {_expires_args(b)}")
    assert a == b and _expires_args(a) == _expires_args(first)


def test_item1_flaky_tests_freeze_the_product_clock_entry():
    """F129 / F180 偶发失败测试改为冻结被测代码既有的时钟入口(不另造时间源); 夹具名与冻结点可 grep 核对。"""
    f129 = (REPO_ROOT / "tests" / "test_aipos_f129_codex_advisor_harness.py").read_text(encoding="utf-8")
    f180 = (REPO_ROOT / "tests" / "test_aipos_f180_grant_request.py").read_text(encoding="utf-8")
    assert 'monkeypatch.setattr(onboarding, "utc_now", lambda: moment)' in f129
    assert "def test_item3_guide_codex_vs_claude_code_default(frozen_guide_clock):" in f129
    assert 'monkeypatch.setattr(clock, "utc_now", lambda: rig["now"])' in f180
    for name in ("test_item1_inbox_auto_suggestion_cli_line(rig, frozen_clock)",
                 "test_item2_inbox_and_envelope_pages_show_request_cards(board, frozen_clock)"):
        assert f"def {name}:" in f180, name
    assert 'driver=f"advisor.{P}.fresh", now=rig["now"])' in f180  # suggest_grant 既有 now 注入口


# ===========================================================================
# 件② kickoff 全量时限句按卡面 harness 的单次工具时限
# ===========================================================================

def _kickoff_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, card: str, harness: str | None) -> Path:
    """chris 形临时治理根(多仓 + 子 agent 执行者)+ 已认领卡; harness=None = 卡面不写 harness(缺省按 task_mode)。"""
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    base = tmp_path / card.lower()
    base.mkdir()
    gov = f78._make_gov(base, monkeypatch, shape="chris", project=CHRIS)
    home = base / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    app = f78._init_product_repo(base / "repos" / "android-app")
    _update_project(gov, code_repo=str(app), repos={"default": "android-app", "items": {"android-app": str(app)}},
                    test_contract={"runall_path": "tests/run-all.sh", "post_merge_regression": {"timeout_seconds": 1500}})
    (gov / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
    _land_subagent(gov, CHRIS_EXEC, project=CHRIS, harness="claude-code", host=__import__("socket").gethostname())
    extra = {"project": CHRIS, "lane": {"repo": "android-app", "paths": ["app/"]}}
    if harness:
        extra["harness"] = harness
    f78._card(gov, card, "pending", assigned=CHRIS_EXEC, extra=extra)
    claimed = mutate_queue_task(gov, "claim", task_id=card, actor=CHRIS_EXEC, dry_run=False, profiles=load_agent_profiles(gov),
                                with_records=True)
    assert claimed.get("moved"), claimed
    return gov


def _runall_line(gov: Path, card: str) -> tuple[str, dict]:
    rc, brief, err = _cli("kickoff", "--task-id", card, "--workspace-root", str(gov))
    assert rc == declared_exit_code(KICKOFF, "ok"), err
    rc, out, err = _cli("kickoff", "--task-id", card, "--workspace-root", str(gov), "--json")
    assert rc == 0, err
    line = next(ln for ln in brief.splitlines() if ln.startswith("- 全量命令: "))
    return line, json.loads(out)


def test_item2_harness_tool_timeout_declaration():
    from tools.aipos_cli.subagent_kickoff import harness_tool_timeout

    got = {h: harness_tool_timeout(h) for h in ("claude-code", "pi", "codex")}
    _show(f"[件②·harness 单次工具时限声明] {got}")
    assert got == {"claude-code": 600, "pi": None, "codex": None}


def test_item2_harness_tool_timeout_fails_closed(monkeypatch: pytest.MonkeyPatch):
    import tools.schema_loader as schema_loader
    from tools.aipos_cli.subagent_kickoff import harness_tool_timeout
    from tools.schema_loader import SchemaLoadError

    real = schema_loader.load_schema

    def fake(name, *a, **kw):
        data = real(name, *a, **kw)
        if name != "enums":
            return data
        data = json.loads(json.dumps(data))
        for item in data["enums"]["harness"]["values"]:
            if item["value"] == "pi":
                item["tool_timeout_seconds"] = "600"
        return data

    monkeypatch.setattr(schema_loader, "load_schema", fake)
    with pytest.raises(SchemaLoadError) as exc:
        harness_tool_timeout("pi")
    _show(f"[件②·时限值非正整数 = 声明错误] {exc.value}")
    assert "tool_timeout_seconds" in str(exc.value) and "正整数" in str(exc.value)
    with pytest.raises(SchemaLoadError) as exc:
        harness_tool_timeout("gemini")
    assert "gemini" in str(exc.value)


def test_item2_kickoff_runall_timing_by_card_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # claude-code 卡: 全量时限(post_merge_regression.timeout_seconds 1500, 同 lybra 项目声明)> 600 秒 = 一律 run-long, 不写「设足」
    gov = _kickoff_rig(tmp_path, monkeypatch, card="HBX-81", harness="claude-code")
    line, data = _runall_line(gov, "HBX-81")
    _show(f"---- 件②·claude-code 卡(全量时限超工具时限)全量句原文 ----\n{line}")
    assert data["sources"]["runall_timing"] == "run_long" and data["values"]["harness"] == "claude-code"
    assert "超过本卡 harness claude-code 的单次工具时限 600 秒(来源 enums.schema harness.claude-code.tool_timeout_seconds)" in line
    assert "全量一律按下条 run-long 起跑并分段等完, 不同步跑" in line and "设足" not in line

    # 同卡, 项目把全量时限声明到工具时限内 = 同步跑并写设足秒数
    pj = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    _update_project(gov, test_contract={**pj["test_contract"], "post_merge_regression": {"timeout_seconds": 300}})
    line, data = _runall_line(gov, "HBX-81")
    _show(f"---- 件②·claude-code 卡(全量时限 300 秒, 在工具时限内)全量句原文 ----\n{line}")
    assert data["sources"]["runall_timing"] == "sync"
    assert "在本卡 harness claude-code 的单次工具时限 600 秒内" in line and "同步跑, bash 工具超时设足: `300` 秒" in line

    # 未声明工具时限的 harness(codex)= 不写秒数只教 run-long
    gov = _kickoff_rig(tmp_path, monkeypatch, card="HBX-82", harness="codex")
    line, data = _runall_line(gov, "HBX-82")
    _show(f"---- 件②·codex 卡(harness 未声明工具时限)全量句原文 ----\n{line}")
    assert data["sources"]["runall_timing"] == "undeclared"
    assert "本卡 harness codex 未声明单次工具时限(enums.schema harness.codex.tool_timeout_seconds 无值)" in line
    assert "不按秒数同步跑" in line and not re.search(r"\d+` 秒|\d+ 秒", line)

    # 卡面不写 harness = 缺省按 task_mode(card.schema intent_face.harness.default_by_task_mode: code → pi), pi 未声明 = 同上
    gov = _kickoff_rig(tmp_path, monkeypatch, card="HBX-83", harness=None)
    line, data = _runall_line(gov, "HBX-83")
    _show(f"---- 件②·卡面无 harness(缺省 pi)全量句原文 ----\n{line}")
    assert data["values"]["harness"] == "pi" and data["sources"]["runall_timing"] == "undeclared"
    assert not re.search(r"\d+` 秒|\d+ 秒", line)


def test_item2_kickoff_texts_single_source():
    """时限句只在 verbs.schema lybra_kickoff texts; 旧「bash 工具超时设足: {runall_timeout}」整句退役(只留 sync 态措辞)。"""
    from tools.aipos_cli.subagent_kickoff import kickoff_contract

    texts = kickoff_contract()["texts"]
    assert texts["verify_runall"] == "- 全量命令: {runall_command}; {runall_timing}"
    assert sum("设足" in v for v in texts.values()) == 1 and "设足" in texts["runall_timing_sync"]
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert not re.search(r"\b600\b", src) and '"设足' not in src  # 秒数零写死, 措辞只在 texts


# ===========================================================================
# 件③ tools/lybra-deploy 不在产品仓检出写 pyc
# ===========================================================================

def test_item3_deploy_script_no_bytecode_in_checkout(tmp_path: Path):
    repo = tmp_path / "checkout"
    (repo / "tools" / "aipos_cli").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "tools" / "lybra-deploy", repo / "tools" / "lybra-deploy")
    for rel in ("tools/__init__.py", "tools/aipos_cli/__init__.py"):
        (repo / rel).write_text("", encoding="utf-8")
    (repo / "tools" / "aipos_cli" / "aipos_cli.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}

    def caches() -> list[str]:
        return sorted(str(p.relative_to(repo)) for p in repo.rglob("__pycache__"))

    proc = subprocess.run(["bash", "tools/lybra-deploy", "validate"], cwd=str(repo), env=env, capture_output=True, text=True, timeout=60)
    _show(f"[件③·lybra-deploy validate(环境未设 PYTHONDONTWRITEBYTECODE)] exit {proc.returncode}\n{proc.stdout}{proc.stderr}"
          f"[件③·预演后检出内 __pycache__] {caches() or '无'}")
    assert proc.returncode == 0 and "Import validation passed" in proc.stdout + proc.stderr
    assert caches() == []
    # 对照: 同一导入不经本脚本 = 写 pyc(证明探针灵敏, 上面的「无」不是空测)
    subprocess.run([sys.executable, "-c", "import tools.aipos_cli.aipos_cli"], cwd=str(repo), env=env, check=True)
    _show(f"[件③·对照(直接 python3 导入)后 __pycache__] {caches()}")
    assert caches() == ["tools/__pycache__", "tools/aipos_cli/__pycache__"]


def test_item3_deploy_script_export_precedes_every_python_call():
    lines = (REPO_ROOT / "tools" / "lybra-deploy").read_text(encoding="utf-8").splitlines()
    export = lines.index("export PYTHONDONTWRITEBYTECODE=1")
    calls = [i for i, ln in enumerate(lines) if re.search(r"\bpython3\b", ln) and not ln.lstrip().startswith("#")]
    _show(f"[件③·统一导出行 {export + 1}; python3 调用行] {[i + 1 for i in calls]}")
    assert calls and export < min(calls)
    assert all("PYTHONDONTWRITEBYTECODE" not in ln for i, ln in enumerate(lines) if i != export and not ln.lstrip().startswith("#"))


# ===========================================================================
# 件④ 退役 AIPOS-276 两条永远失败的测试
# ===========================================================================

def test_item4_retired_aipos276_tests_and_known_failure_lines():
    from tools.aipos_cli.workspace_config import runall_directives

    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "task_cards/AIPOS-276/" not in runall
    assert not (REPO_ROOT / "task_cards" / "AIPOS-276" / "test_fix1.py").exists()
    t276 = (REPO_ROOT / "task_cards" / "AIPOS-276" / "test_aipos276.py").read_text(encoding="utf-8")
    assert "def test_s2_stale_map_publish_warn" not in t276 and "test_s2_stale_map_publish_warn()" not in t276
    directives = runall_directives(runall)
    _show(f"[件④·run-all 声明解析] discover={directives['discover']} exclude={len(directives['exclude'])} "
          f"known_failures={len(directives['known_failures'])}")
    assert not [t for t in directives["known_failures"] if t.startswith("task_cards/AIPOS-276/")]
    out = subprocess.run(["git", "grep", "-n", "-E", r"test_s2_stale_map_publish_warn|test_f276_1_warnings_in_publish_record|test_fix1\.py",
                          "--", ":!task_cards/AIPOS-276/*.md", ":!tests/test_aipos_f189_small_sweep.py"],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    _show(f"[件④·退役测试名 git grep(历史 md 除外)] exit {out.returncode}: {out.stdout.strip() or '无'}")
    assert out.returncode == 1 and out.stdout == ""
