"""AIPOS-F121 件①/③ — 仓根 conftest.py 会话层 HOME 隔离覆盖全部测试目录(复用 F116 唯一隔离函数, 禁第二实现)。

原状(F116 缺口 6): F116 只在 tests/、tools/aipos_cli/、tools/mcp_server/tests/、web/ 放 conftest; 仓根、tools/test_*.py、
tools/lybra_tui/tests、tools/sandbox_runtime/tests、task_cards/* 的测试直接跑 pytest 时仍借真实 HOME/LYBRA_HOME_ROOT
(tools/test_aipos_r1_scope.py 就是在这里读到真实工位凭据)。现: 仓根 conftest.py 调 runall_discovery.isolate_test_session()。

验收(子进程直跑 pytest, 真实形 HOME = tmp 下带 ~/.lybra/config.json 的假家目录, 另设 LYBRA_HOME_ROOT, 不碰真实环境):
 ① run-all 自动发现集里**每个测试目录**各取一个 .py 测试文件, 收集期探针看到的 HOME 已换成会话临时目录、LYBRA_HOME_ROOT 已清
 ② 对照: 同一文件加 --noconftest → 探针看到真实形 HOME(证明是 conftest 在隔离, 探针本身不改环境)
 ③ 仓根 conftest 只调唯一实现, 不自写环境构造
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.runall_discovery import ISOLATED_HOME_ENV, build_plan  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract  # noqa: E402

PROBE = '''
import json, os
def pytest_sessionstart(session):
    with open(os.environ["F121_PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump({"HOME": os.environ.get("HOME"), "LYBRA_HOME_ROOT": os.environ.get("LYBRA_HOME_ROOT")}, fh)
'''


def _one_file_per_test_dir() -> dict[str, str]:
    plan = build_plan(REPO_ROOT, "tests/run-all.sh", default_test_contract())
    picked: dict[str, str] = {}
    for rel in plan["to_run"]:
        if rel.endswith(".py"):
            picked.setdefault(rel.rsplit("/", 1)[0] if "/" in rel else ".", rel)
    return picked


def _probe(tmp_path: Path, rel: str, *extra: str) -> dict:
    real_home = tmp_path / "real-shaped-home"
    fake_projects = tmp_path / "real-shaped-projects"
    (real_home / ".lybra").mkdir(parents=True, exist_ok=True)
    fake_projects.mkdir(exist_ok=True)
    (real_home / ".lybra" / "config.json").write_text(json.dumps({"config_version": 1, "home_root": str(fake_projects)}),
                                                      encoding="utf-8")
    probe_dir = tmp_path / "probe"
    probe_dir.mkdir(exist_ok=True)
    (probe_dir / "f121_probe.py").write_text(PROBE, encoding="utf-8")
    out = tmp_path / f"probe-{abs(hash((rel,) + extra))}.json"
    env = {k: v for k, v in os.environ.items() if k != ISOLATED_HOME_ENV and not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"HOME": str(real_home), "LYBRA_HOME_ROOT": str(fake_projects), "F121_PROBE_OUT": str(out),
                "PYTHONPATH": os.pathsep.join([str(probe_dir), str(REPO_ROOT)])})
    proc = subprocess.run([sys.executable, "-m", "pytest", "--co", "-q", "-p", "no:cacheprovider", "-p", "f121_probe",
                           *extra, rel], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300)
    assert out.is_file(), f"探针未落盘({rel}, exit={proc.returncode}):\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
    seen = json.loads(out.read_text(encoding="utf-8"))
    seen["real_home"] = str(real_home)
    return seen


def test_every_test_dir_is_isolated_by_conftest(tmp_path):
    picked = _one_file_per_test_dir()
    for must in (".", "tools", "tools/lybra_tui/tests", "tools/sandbox_runtime/tests", "tests", "web/board/tests"):
        assert must in picked, f"自动发现集缺测试目录 {must}: {sorted(picked)}"
    assert any(d.startswith("task_cards/") for d in picked), sorted(picked)
    for directory, rel in sorted(picked.items()):
        seen = _probe(tmp_path, rel)
        print(f"[①] {directory:<32} {rel}: 真实形 HOME={seen['real_home']} → 会话 HOME={seen['HOME']} "
              f"LYBRA_HOME_ROOT={seen['LYBRA_HOME_ROOT']}")
        assert seen["HOME"] != seen["real_home"] and Path(seen["HOME"]).name.startswith("lybra-test-home-"), (rel, seen)
        assert seen["LYBRA_HOME_ROOT"] is None, (rel, seen)


def test_control_without_conftest_sees_real_shaped_home(tmp_path):
    for rel in ("test_f003_cli_return_block_atomicity.py", "tools/test_aipos_r1_scope.py"):
        seen = _probe(tmp_path, rel, "--noconftest")
        print(f"[② 对照 --noconftest] {rel}: HOME={seen['HOME']} LYBRA_HOME_ROOT={seen['LYBRA_HOME_ROOT']}")
        assert seen["HOME"] == seen["real_home"] and seen["LYBRA_HOME_ROOT"], seen


def test_root_conftest_reuses_single_isolation():
    src = (REPO_ROOT / "conftest.py").read_text(encoding="utf-8")
    assert "from tools.aipos_cli.runall_discovery import isolate_test_session" in src
    assert "isolate_test_session()" in src
    for forbidden in ("os.environ[", "os.environ.update", "putenv", "mkdtemp", '"HOME"'):
        assert forbidden not in src, forbidden
    rd = (REPO_ROOT / "tools" / "aipos_cli" / "runall_discovery.py").read_text(encoding="utf-8")
    assert rd.count("def isolate_test_session(") == 1 and rd.count("def isolated_test_env(") == 1
