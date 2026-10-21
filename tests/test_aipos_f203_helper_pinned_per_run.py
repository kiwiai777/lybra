"""AIPOS-F203 — 验证 helper 按 run 固定版本(chris 理想 HBLX-20CV 实撞, gap #208: 项目脚本写死 helper 摘要, 产品升级 helper 即被打断)。

件① run-start --confirm 把当时部署的 helper 原文存入快照库(project.json paths.validation_helper_root, 内容寻址 <sha256>.py.txt, 非代码扩展名),
    run 记录 runs[].helper_sha256 记摘要; 该 run 的 init-run / src-verify / evidence-* 命令取用 = `… helper --id <声明> --run-id <run>`,
    一律下发这份快照(部署升级后仍是旧快照); 新 run 固定新版。快照随 project.json 同一提交落账。
件② 查询口 `lybra project validation-root helper --id <声明> --run-id <run> [--sha256]`(项目脚本运行时取值, 禁写死); 开工稿 / 章程新句。
件③ 旧 run(无 helper_sha256)= 按当前部署版并标注(stderr / list / 开工稿)。快照被改 / 缺失 = VR_HELPER_SNAPSHOT_INVALID(不下发)。

靶场 = F190 chris 形靶场(同一 rig: 临时治理根 + 临时治理仓 / 远端裸仓 + 模拟远端主机 sim-mac = 临时目录); 「部署升级」= 本进程把产品
helper 原文换成带 HELPER_V2 标记的新版(monkeypatch validation_root.helper_source, 真实 helper 文件不动); 目标主机侧 = 产品铸的管道命令
左半段经进程内 CLI 取 helper、右半段 `python3 - <子命令>` 子进程运行(与开工稿同一运行方式)。禁真门 / 真治理根 / 真工位 / 真 pi / 真 Mac / 真 chris。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import ast
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f190_validation_root import (  # noqa: E402,F401  — 靶场唯一来源(F190 chris 形 rig)
    ADVISOR, AUD, CARD, EXEC, HELPER, _cli, _declare_both, _fp, _run_start, _show, _vr, rig,
)

EXEC_DECL = "vr-hbx-60-executor-1"
MARK = "HELPER_V2"


def _upgraded(source: str) -> str:
    """模拟产品升级后的 helper: 运行即在 stderr 打 HELPER_V2(其余行为不变)。"""
    return source.replace("\nimport argparse\n", f"\nimport argparse\nimport sys as _f203\n_f203.stderr.write('{MARK}\\n')\n", 1)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(gov: Path, decl_id: str = EXEC_DECL) -> dict:
    from tools.aipos_cli.validation_root import project_validation_roots

    return next(e for e in project_validation_roots(gov) if e["id"] == decl_id)


def _run(gov: Path, run_id: str, decl_id: str = EXEC_DECL) -> dict:
    return next(r for r in _entry(gov, decl_id)["runs"] if r["run_id"] == run_id)


def _host_pipe(command: str) -> tuple[int, str, str]:
    """执行产品铸的本机形管道命令 `<lybra … helper --id … --run-id …> | python3 - <子命令 …>`: 左半段经进程内 CLI(同一代码, 可见模拟升级),
    取其 stdout(= 下发的 helper 原文)交右半段子进程标准输入。返回 (退出码, 输出, 下发的 helper 原文)。"""
    left, right = command.split(" | python3 - ", 1)
    argv = shlex.split(left)
    assert argv[0] == "lybra" and argv[3:6] == ["project", "validation-root", "helper"] and "--id" in argv and "--run-id" in argv, left
    rc, source, err = _cli(*argv[1:])
    assert rc == 0, err
    proc = subprocess.run([sys.executable, "-", *shlex.split(right)], input=source, capture_output=True, text=True, timeout=120)
    return proc.returncode, (proc.stderr + proc.stdout).strip(), source  # 末行 = helper 结论行(INIT_OK / SRC_OK / EVIDENCE_OK)


def _query(gov: Path, run_id: str, *extra: str, decl_id: str = EXEC_DECL) -> tuple[int, str, str]:
    return _cli("--workspace-root", gov, "project", "validation-root", "helper", "--id", decl_id, "--run-id", run_id, *extra)


# ===========================================================================
# 件① run-start 记快照 → 部署升级 → 同一 run 的 init-run / src-verify / evidence 仍下发旧快照; 新 run 取新版
# ===========================================================================

def test_item1_run_pinned_across_upgrade_new_run_takes_new(rig, monkeypatch):
    from tools.aipos_cli import validation_root as vr

    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    old_source = HELPER.read_text(encoding="utf-8")
    old_sha = _sha(old_source)
    rc, out, err = _vr(gov, "run-start", "--id", EXEC_DECL, "--candidate", rig["candidate"], "--actor", ADVISOR)
    _show(f"---- 件① run-start 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and f'"helper_sha256": "{old_sha}"' in out and "确认时新存" in out
    snap_dir = gov / "5_tasks" / "validation_root_helpers"
    assert not snap_dir.exists(), "预演零写入(快照不落)"
    rc, out, err = _run_start(rig, EXEC_DECL)
    _show(f"---- 件① run-start --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"本 run 固定验证 helper sha256 = {old_sha}" in out and "新存" in out
    snapshot = snap_dir / f"{old_sha}.py.txt"
    assert snapshot.read_bytes() == HELPER.read_bytes() and _run(gov, "run-1")["helper_sha256"] == old_sha
    tracked = f78._git(gov, "ls-files", "--", "project.json", f"5_tasks/validation_root_helpers/{old_sha}.py.txt").splitlines()
    log = f78._git(gov, "log", "-1", "--format=%s", "--name-only")
    _show(f"[件① 落账] 最近一次治理仓提交:\n{log}")
    assert sorted(tracked) == sorted(["project.json", f"5_tasks/validation_root_helpers/{old_sha}.py.txt"]) and "validation-root run-start" in log

    # 模拟部署升级: 产品 helper 原文换成新版
    new_source = _upgraded(old_source)
    new_sha = _sha(new_source)
    assert new_sha != old_sha
    monkeypatch.setattr(vr, "helper_source", lambda: new_source)
    rc, cur, _ = _cli("project", "validation-root", "helper", "--sha256")
    _show(f"[件① 升级后] 当前部署版 helper sha256 = {cur.strip()}(旧 {old_sha})")
    assert rc == 0 and cur.strip() == new_sha

    entry, run = _entry(gov), _run(gov, "run-1")
    init_cmd = vr.init_run_command(gov, entry, run)
    delivery = vr.source_delivery(gov, entry, run)
    manifest_cmd = vr.evidence_manifest_command(gov, entry, run)
    fetch = f"lybra --workspace-root {gov} project validation-root helper --id {EXEC_DECL} --run-id run-1"
    for label, cmd in (("init-run", init_cmd), ("src-verify", delivery["verify"]), ("evidence-manifest", manifest_cmd)):
        _show(f"[件① 升级后同一 run 铸的 {label} 命令] {cmd[:220]}…")
        assert cmd.startswith(f"{fetch} | python3 - {label} "), cmd
    rig["mac"].mkdir(parents=True)
    rc, host_out, served = _host_pipe(init_cmd)
    _show(f"---- 件① 升级后 run-1 init-run(下发 sha256={_sha(served)})exit {rc} ----\n{host_out}")
    assert rc == 0 and _sha(served) == old_sha and MARK not in host_out and host_out.splitlines()[-1].startswith("INIT_OK ")
    run_dir = base / CARD / "run-1"
    subprocess.run(delivery["deliver"], shell=True, check=True, capture_output=True, timeout=120)
    rc, host_out, served = _host_pipe(delivery["verify"])
    _show(f"---- 件① 升级后 run-1 src-verify(下发 sha256={_sha(served)})exit {rc} ----\n{host_out}")
    assert rc == 0 and _sha(served) == old_sha and MARK not in host_out and host_out.splitlines()[-1].startswith("SRC_OK ")
    (run_dir / "logs").mkdir()
    (run_dir / "logs" / "run.log").write_text("验证日志\n", encoding="utf-8")
    rc, host_out, served = _host_pipe(manifest_cmd)
    _show(f"---- 件① 升级后 run-1 evidence-manifest(下发 sha256={_sha(served)})exit {rc} ----\n{host_out}")
    assert rc == 0 and _sha(served) == old_sha and MARK not in host_out and host_out.splitlines()[-1].startswith("EVIDENCE_OK ")
    got = vr.run_helper(gov, entry, run)
    assert got["state"] == "pinned" and got["sha256"] == old_sha and got["source"] == old_source, "证据取回送入目标主机的同一读取口"
    rc, out, _ = _query(gov, "run-1", "--sha256")
    _show(f"[件① 升级后查询 run-1 --sha256] {out.strip()}")
    assert rc == 0 and out.strip() == old_sha

    # 新 run 取新版
    rc, out, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    rc, out, err = _run_start(rig, EXEC_DECL)
    _show(f"---- 件① 升级后新 run-start --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"本 run 固定验证 helper sha256 = {new_sha}" in out
    assert (snap_dir / f"{new_sha}.py.txt").read_text(encoding="utf-8") == new_source and snapshot.read_bytes() == HELPER.read_bytes(), \
        "旧快照保留(run-finish 后供审计复核), 新版另存一份"
    run2 = _run(gov, "run-2")
    rc, host_out, served = _host_pipe(vr.init_run_command(gov, entry := _entry(gov), run2))
    _show(f"---- 件① 新 run-2 init-run(下发 sha256={_sha(served)})exit {rc} ----\n{host_out}")
    assert rc == 0 and _sha(served) == new_sha and MARK in host_out and host_out.splitlines()[-1].startswith("INIT_OK ")
    rc, out1, _ = _query(gov, "run-1", "--sha256")
    rc2, out2, _ = _query(gov, "run-2", "--sha256")
    _show(f"[件① 两 run 各自固定] run-1={out1.strip()} run-2={out2.strip()}")
    assert (rc, rc2, out1.strip(), out2.strip()) == (0, 0, old_sha, new_sha)
    rc, listing, _ = _vr(gov, "list")
    _show(f"---- 件① list ----\n{listing}")
    assert f"helper sha256={old_sha}(本 run 固定)" in listing and f"helper sha256={new_sha}(本 run 固定)" in listing
    assert entry["runs"][-1]["helper_sha256"] == new_sha


# ===========================================================================
# 件② 查询口按 run 输出 / --sha256 原文与拒因
# ===========================================================================

def test_item2_query_by_run_and_refusals(rig):
    gov = rig["gov"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0
    sha = _run(gov, "run-1")["helper_sha256"]
    rc, out, err = _query(gov, "run-1")
    _show(f"[件② helper --id {EXEC_DECL} --run-id run-1] exit {rc} 首行 {out.splitlines()[0]!r} 共 {len(out)} 字符 sha256={_sha(out)} stderr={err!r}")
    assert rc == 0 and out == HELPER.read_text(encoding="utf-8") and _sha(out) == sha and err == ""
    rc, out, err = _query(gov, "run-1", "--sha256")
    _show(f"[件② helper --id {EXEC_DECL} --run-id run-1 --sha256] exit {rc}: {out.strip()}")
    assert rc == 0 and out == f"{sha}\n"
    rc, out, err = _query(gov, "run-1", "--sha256", decl_id=f"{EXEC_DECL}")  # 项目名省略 = --workspace-root 定项目
    assert rc == 0 and out.strip() == sha

    rc, out, err = _query(gov, "run-9")
    _show(f"[件② 不存在的 run] exit {rc}: {err.strip()}")
    assert rc == 1 and out == "" and "VR_NOT_FOUND" in err and "run-9" in err
    rc, out, err = _cli("--workspace-root", gov, "project", "validation-root", "helper", "--id", EXEC_DECL)
    _show(f"[件② 只给 --id] exit {rc}: {err.strip()}")
    assert rc == 2 and out == "" and "--id 与 --run-id 须同给" in err

    snapshot = gov / "5_tasks" / "validation_root_helpers" / f"{sha}.py.txt"
    original = snapshot.read_bytes()
    snapshot.write_bytes(original + b"# tampered\n")
    rc, out, err = _query(gov, "run-1")
    _show(f"[件② 快照被改] exit {rc}: {err.strip()}")
    assert rc == 1 and out == "" and "VR_HELPER_SNAPSHOT_INVALID" in err and "被改" in err and "重新 run-start" in err
    rc, brief, _ = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    pin = next(ln for ln in brief.splitlines() if "验证 helper 按 run 固定(AIPOS-F203)" in ln)
    _show(f"[件② 快照被改时开工稿] {pin}")
    assert "快照不可用" in pin and "停线报告" in pin
    snapshot.unlink()
    rc, out, err = _query(gov, "run-1", "--sha256")
    _show(f"[件② 快照缺失] exit {rc}: {err.strip()}")
    assert rc == 1 and out == "" and "VR_HELPER_SNAPSHOT_INVALID" in err and "缺失" in err
    snapshot.write_bytes(b"not the helper\n")
    rc, out, err = _run_start(rig, "vr-hbx-60r-auditor-1")
    _show(f"[件② 快照库同名文件内容不符时 run-start] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_HELPER_SNAPSHOT_INVALID" in err and "project.json 未改动" in err
    assert all("run-1" != r["run_id"] for r in _entry(gov, "vr-hbx-60r-auditor-1")["runs"])
    snapshot.write_bytes(original)
    assert _query(gov, "run-1", "--sha256")[1].strip() == sha


# ===========================================================================
# 件③ 旧 run(无快照)兼容
# ===========================================================================

def test_item3_legacy_run_without_snapshot(rig):
    from tools.aipos_cli import validation_root as vr

    gov = rig["gov"]
    _declare_both(rig)
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    for e in data["validation_roots"]["declarations"]:  # 靶场自造人肉期 / F203 前的 run 记录(无 helper_sha256)
        if e["id"] == EXEC_DECL:
            e["runs"] = [{"run_id": "run-1", "candidate": rig["candidate"], "started_at": "2026-10-20T00:00:00Z", "started_by": ADVISOR,
                          "finished_at": None, "finished_by": None}]
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    current = vr.helper_sha256()
    rc, out, err = _query(gov, "run-1", "--sha256")
    _show(f"[件③ 旧 run --sha256] exit {rc} stdout={out.strip()} stderr={err.strip()}")
    assert rc == 0 and out.strip() == current and "旧 run 无固定 helper 快照" in err and f"按当前部署版 {current} 下发" in err
    rc, out, err = _query(gov, "run-1")
    assert rc == 0 and out == HELPER.read_text(encoding="utf-8"), "旧 run 原文 = 当前部署版(stdout 只有原文, 标注走 stderr)"
    rc, listing, _ = _vr(gov, "list")
    _show(f"---- 件③ 旧 run list ----\n{listing}")
    assert "helper 未固定(旧 run, run-start 早于 AIPOS-F203: 取用按当前部署版)" in listing
    rc, brief, _ = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    pin = next(ln for ln in brief.splitlines() if "验证 helper 按 run 固定(AIPOS-F203)" in ln)
    _show(f"[件③ 旧 run 开工稿] {pin}")
    assert "旧 run 无固定 helper 快照" in pin and f"--id {EXEC_DECL} --run-id run-1 --sha256" in pin
    cmd = vr.init_run_command(gov, _entry(gov), _run(gov, "run-1"))
    assert f"helper --id {EXEC_DECL} --run-id run-1 | python3 - init-run " in cmd, "旧 run 同一取用命令(按当前版下发)"


# ===========================================================================
# 件② 开工稿 / 章程新句(同源)
# ===========================================================================

def test_item4_kickoff_and_charter_sentence(rig):
    gov = rig["gov"]
    _declare_both(rig)
    rc, brief, _ = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    pin = next(ln for ln in brief.splitlines() if "验证 helper 按 run 固定(AIPOS-F203)" in ln)
    _show(f"[件② 开工稿(未起 run)] {pin}")
    assert "起 run(run-start --confirm)时固定当时部署版" in pin and f"--id {EXEC_DECL} --run-id <run-id> --sha256" in pin
    assert _run_start(rig, EXEC_DECL)[0] == 0
    sha = _run(gov, "run-1")["helper_sha256"]
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    pin = next(ln for ln in brief.splitlines() if "验证 helper 按 run 固定(AIPOS-F203)" in ln)
    query = f"lybra --workspace-root {gov} project validation-root helper --id {EXEC_DECL} --run-id run-1 --sha256"
    _show(f"[件② 开工稿(run-1 活动)] {pin}")
    for needle in (f"本 run `run-1` 固定 helper sha256 = {sha}", "产品升级部署不影响进行中 run", f"运行时取 `{query}` 的输出",
                   "禁在脚本 / 卡面写死摘要"):
        assert needle in pin, needle
    helper_line = next(ln for ln in brief.splitlines() if "目标主机侧 realpath 判定" in ln)
    _show(f"[件② 开工稿 helper 取用行] {helper_line}")
    assert f"`lybra --workspace-root {gov} project validation-root helper --id {EXEC_DECL} --run-id run-1`" in helper_line
    assert f"核 sha256 = {sha}" in helper_line
    rc, charter, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", gov)
    section = charter[charter.index("## 🔴 写权限边界"):]
    assert rc == 0 and pin in section, "章程写权限节同源(同一句)"
    rc, charter, err = _cli("charter", "--role", "auditor", "--instance", AUD, "--workspace-root", gov)
    assert rc == 0 and "验证 helper 按 run 固定(AIPOS-F203)" in charter and "--id vr-hbx-60r-auditor-1 --run-id <run-id> --sha256" in charter


# ===========================================================================
# 单源 / 声明 / 发现
# ===========================================================================

def test_single_source_and_declarations():
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli.governance_guardrails import is_code_file, load_guardrail_declarations
    from tools.aipos_cli.workspace_config import PROJECT_PATH_KEYS

    src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    callers = sorted({fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                      for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "helper_source"})
    _show(f"[单源] helper_source() 调用方 = {callers}")
    assert callers == ["helper_sha256", "pin_helper_snapshot", "run_helper"], "当前部署版只在固定口 / 读取口取, 铸造口一律经本 run 固定 helper"
    literal = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)
               and any(isinstance(v, ast.Constant) and "project validation-root helper" in str(v.value) for v in n.values)]
    fetch_fn = next(fn for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name == "helper_fetch_command")
    assert literal and all(fetch_fn.lineno <= ln <= fetch_fn.end_lineno for ln in literal), "helper 取用命令唯一铸造口 helper_fetch_command"
    assert "except Exception" not in src
    decl = vr.helper_pinning_declaration()
    assert not is_code_file(decl["snapshot_filename"].replace("{sha256}", "0" * 64), load_guardrail_declarations())
    assert "helper_sha256" in vr.RUN_OPTIONAL_KEYS and "VR_HELPER_SNAPSHOT_INVALID" in vr.reject_codes()
    assert PROJECT_PATH_KEYS[-1] == "validation_helper_root" and "helper_pin" in vr.TEXT_KEYS


def test_f203_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f203_helper_pinned_per_run.py", default_test_contract())
    assert is_test_file("tests/test_aipos_f203_helper_compat_notice.py", default_test_contract())
