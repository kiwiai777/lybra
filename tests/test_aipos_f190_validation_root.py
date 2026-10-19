"""AIPOS-F190 — 跨机临时验证根声明(按 项目 / 主机 / 实例 / 卡 / run 的有限可写根)。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi / 真 Mac):
  - chris 形临时治理根(chris 落点声明 + 多仓 repos.items), 执行者 = Codex 子 agent 在他机(land host=advmac), 审计者 = 本机 pi 工位;
    临时治理仓 + 临时远端裸仓(写后自动落账可核 git log); 认领走门认领核心 mutate_queue_task claim。
  - 模拟远端主机 = 主机名注入 `sim-mac` + 临时目录 <tmp>/mac/Users/kiwi/...(治理根侧只做字面判定);
    目标主机侧 realpath 判定 = 在临时目录对 helper 原文件起子进程(`python3 validation_root_helper.py check`, 与目标主机同一份)。
  - 本机主机(host = 本机标签)另起一条声明: 治理根侧以 realpath 判符号链接逃逸。

验收原文全部经 _show 打到 stdout(RETURN 证据): ①声明两阶段 / 落账 / 禁区拒; ②run 绑定与跨卡 / 跨 run / 已结束 run / 审计写执行 run 拒;
③.. 穿越与符号链接逃逸拒; ④撤销 / 到期 / 主机指纹不符拒; ⑤⑥chris 形开工稿与章程(含 Codex 沙箱可写根、iOS / Android / Gradle 重定向)。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f95_harness_launch import _land as _land_ws, _workstation  # noqa: E402
from test_aipos_f187_kickoff_worktree_cleanup import _land_subagent  # noqa: E402

CHRIS = "chris-demo"
EXEC = f"exec.{CHRIS}.advmac"
AUD = f"audit.{CHRIS}.devbox"
ADVISOR = f"advisor.{CHRIS}.advmac"
CARD, OTHER = "HBX-60", "HBX-61"
AUDIT_CARD = f"{CARD}R"
HOST = "sim-mac"
HELPER = REPO_ROOT / "tools" / "aipos_cli" / "validation_root_helper.py"
OWNER_REF = "decision_log 2026-10-19 Owner 授权内部隔离验证"


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    text = "".join(ln for ln in out.getvalue().splitlines(keepends=True) if not ln.startswith("治理根: "))
    return int(rc or 0), text, err.getvalue()


def _helper(*argv: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(HELPER), *argv], capture_output=True, text=True, timeout=60)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _vr(gov: Path, action: str, *argv: str) -> tuple[int, str, str]:
    return _cli("project", "validation-root", action, "--workspace-root", gov, *argv)


def _wb(gov: Path, *argv: str) -> tuple[int, str, str]:
    return _cli("roles", "--workspace-root", gov, "write-boundary", *argv)


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    from tools.aipos_cli.agent_profiles import load_agent_profiles
    from tools.aipos_cli.queue_mutation import mutate_queue_task

    gov = f78._make_gov(tmp_path, monkeypatch, shape="chris", project=CHRIS)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    web = f78._init_product_repo(tmp_path / "repos" / "web6")
    app = f78._init_product_repo(tmp_path / "repos" / "android-app")
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    data.update(code_repo=str(web), repos={"default": "web6", "items": {"web6": str(web), "android-app": str(app)}},
                workstations={EXEC: {"gate_ssh_alias": "gate-dev", "material_access": "经 ssh gate-dev 读写治理根与产品仓"}})
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (gov / "5_tasks" / "records" / "sessions").mkdir(parents=True, exist_ok=True)
    _land_subagent(gov, EXEC, project=CHRIS, harness="codex", host="advmac")
    ws = _workstation(home, gov, "chris-auditor", "auditor", AUD)
    _land_ws(gov, AUD, "auditor", ws)
    for card in (CARD, OTHER):
        f78._card(gov, card, "pending", assigned=EXEC,
                  extra={"project": CHRIS, "harness": "codex", "audit_by": AUD, "lane": {"repo": "android-app", "paths": ["app/"]}})
    claimed = mutate_queue_task(gov, "claim", task_id=CARD, actor=EXEC, dry_run=False, profiles=load_agent_profiles(gov), with_records=True)
    assert claimed.get("moved"), claimed
    init_governance_repo(gov)
    mac = tmp_path / "mac" / "Users" / "kiwi" / CHRIS / "scratch"
    return {"gov": gov, "app": app, "web": web, "home": home, "base": mac / CARD, "mac": mac, "tmp": tmp_path,
            "candidate": f78._git(app, "rev-parse", "main")}


def _fp() -> str:
    from tools.aipos_cli.validation_root_helper import host_fingerprint

    return host_fingerprint()  # 「目标主机」上 helper 算出的指纹(靶场在本机跑 helper)


def _declare(r: dict, instance: str, task: str, *extra: str, confirm: bool = True, host: str = HOST, base: Path | None = None):
    argv = ["--host", host, "--base", base or r["base"], "--task-id", task, "--instance", instance, "--owner-ref", OWNER_REF,
            "--actor", ADVISOR, *extra]
    if confirm:
        argv.append("--confirm")
    return _vr(r["gov"], "declare", *argv)


def _declare_both(r: dict) -> None:
    for inst, task in ((EXEC, CARD), (AUD, AUDIT_CARD)):
        rc, out, err = _declare(r, inst, task, "--host-fingerprint", _fp())
        assert rc == 0, out + err


def _run_start(r: dict, decl_id: str, *extra: str) -> tuple[int, str, str]:
    return _vr(r["gov"], "run-start", "--id", decl_id, "--candidate", r["candidate"], "--actor", ADVISOR, "--confirm", *extra)


def _git_log(gov: Path) -> str:
    return f78._git(gov, "log", "-1", "--format=%s%n%b", "--name-only")


# ===========================================================================
# 件① 声明两阶段 / 自动落账 / list / 禁区拒
# ===========================================================================

def test_item1_declare_two_phase_landing_list(rig):
    gov = rig["gov"]
    before = (gov / "project.json").read_text(encoding="utf-8")
    rc, out, err = _declare(rig, EXEC, CARD, confirm=False)
    _show(f"---- 件① declare --dry-run(远端主机未给指纹)exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览(未写; 加 --confirm 写入)" in out and "确认前须补 --host-fingerprint" in out
    assert '"host_fingerprint": null' in out and f"可写目录 = {rig['base']}/{CARD}/<run-id>" in out
    assert (gov / "project.json").read_text(encoding="utf-8") == before, "预演零写入"

    rc, out, err = _declare(rig, EXEC, CARD)
    _show(f"---- 件① declare --confirm 缺指纹 exit {rc} ----\n{out}{err}")
    assert rc == 1 and "VR_HOST_FINGERPRINT_REQUIRED" in err and "validation_root_helper.py fingerprint" in err

    rc, out, err = _declare(rig, EXEC, CARD, "--host-fingerprint", _fp())
    log = _git_log(gov)
    _show(f"---- 件① declare --confirm(执行实例)exit {rc} ----\n{out}{err}---- git log -1 ----\n{log}")
    assert rc == 0 and "已写入" in out and "vr-hbx-60-executor-1" in out and "落账" in out
    assert "validation-root declare" in log and f"{CHRIS}/project.json" in log or "project.json" in log
    pj = (gov / "project.json").resolve()
    assert out.splitlines()[0] == f"目标项目 {CHRIS}(来源: --workspace-root 指定治理根 {gov.resolve()}) · project.json {pj}", out
    assert out.splitlines()[1].startswith(f"project validation-root declare {CHRIS}: 已写入")
    rc, out, err = _declare(rig, AUD, AUDIT_CARD, "--host-fingerprint", _fp())
    _show(f"---- 件① declare --confirm(审计实例, 审计卡未派生: 按被审卡 audit_by 核)exit {rc} ----\n{out}{err}")
    assert rc == 0 and "vr-hbx-60r-auditor-1" in out and f"{rig['base']}/{AUDIT_CARD}/<run-id>" in out

    rc, out, err = _declare(rig, EXEC, CARD, "--host-fingerprint", _fp())
    _show(f"---- 件① 同卡同实例重复声明 exit {rc} ----\n{err}")
    assert rc == 1 and "VR_STATE_CONFLICT" in err

    rc, out, err = _vr(gov, "list")
    _show(f"---- 件① list exit {rc} ----\n{out}")
    assert rc == 0 and "vr-hbx-60-executor-1  [active]" in out and "vr-hbx-60r-auditor-1  [active]" in out
    stored = json.loads((gov / "project.json").read_text(encoding="utf-8"))["validation_roots"]["declarations"]
    assert [e["declared_by"] for e in stored] == [ADVISOR, ADVISOR] and stored[0]["owner_ref"] == OWNER_REF


def test_item1_forbidden_bases_and_ownership_refused(rig):
    gov, app = rig["gov"], rig["app"]
    cases = {
        "/": "禁区目录本身", "/usr/local/vr": "系统路径 /usr", "/System/Volumes/Data/vr": "系统路径 /System",
        "/Library/Developer/vr": "系统路径 /Library", "/private/var/vr": "系统路径 /private", "/Users/kiwi": "用户主目录本身",
        "/Users/kiwi/Library/Caches/vr": "全局缓存", "/Users/kiwi/Library/Developer/CoreSimulator": "全局缓存",
        "/Users/kiwi/Library/Keychains/vr": "全局缓存", "/Users/kiwi/.gradle/vr": "全局缓存", "/Users/kiwi/.android/avd": "全局缓存",
        str(gov / "scratch"): "治理根 / 产品仓", str(app): "治理根 / 产品仓", str(app / ".worktrees" / CARD): "治理根 / 产品仓",
        str(gov.parent): "祖先", "/Users/kiwi/x/../y": "路径穿越", "relative/vr": "绝对路径",
    }
    for base, needle in cases.items():
        rc, _out, err = _vr(gov, "declare", "--host", HOST, "--base", base, "--task-id", CARD, "--instance", EXEC, "--owner-ref", OWNER_REF)
        _show(f"[件① base 禁区] --base {base} → exit {rc}: {err.strip()}")
        assert rc == 1 and "VR_BASE_FORBIDDEN" in err and needle in err, (base, err)
    owner_cases = [
        (EXEC, AUDIT_CARD, "VR_TASK_NOT_OWNED", "审计卡"),       # 执行实例不得声明审计卡目录
        (AUD, CARD, "VR_TASK_NOT_OWNED", "不是审计卡"),           # 审计实例不得声明执行卡目录
        (EXEC, "HBX-404", "VR_TASK_NOT_OWNED", "不在队列"),
        (f"exec.{CHRIS}.ghost", CARD, "VR_INSTANCE_NOT_ENROLLED", "未接入"),
    ]
    for inst, task, code, needle in owner_cases:
        rc, _out, err = _declare(rig, inst, task, confirm=False)
        _show(f"[件① 归属] instance={inst} task={task} → exit {rc}: {err.strip()}")
        assert rc == 1 and code in err and needle in err, err
    rc, _out, err = _declare(rig, EXEC, CARD, "--expires-at", "2099-01-01T00:00:00Z", confirm=False)
    _show(f"[件① 到期上限] exit {rc}: {err.strip()}")
    assert rc == 1 and "max_ttl_hours" in err
    assert "validation_roots" not in json.loads((gov / "project.json").read_text(encoding="utf-8")), "全部拒 = 零写入"


# ===========================================================================
# 件② run 绑定 / 判定(本卡本 run 允许; 他卡 / 他 run / 已结束 run / 审计写执行 run 拒)
# ===========================================================================

def test_item2_run_binding_and_isolation(rig):
    gov, base = rig["gov"], rig["base"]
    # 原缺口复现: 未声明主机路径 = SURFACE_UNDECLARED(chris 10-19 实撞同码)
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", "other-mac", "--check", f"{base}/{CARD}/run-1/x", "--level", "mutate")
    _show(f"[件② 未声明主机] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [SURFACE_UNDECLARED]" in out
    _declare_both(rig)
    rc, out, err = _run_start(rig, "vr-hbx-60-executor-1")
    _show(f"---- 件② run-start(执行)exit {rc} ----\n{out}{err}")
    assert rc == 0 and "run run-1 候选" in out and f"本次可写目录: {base}/{CARD}/run-1" in out
    rc, _o, err = _vr(gov, "run-start", "--id", "vr-hbx-60r-auditor-1", "--candidate", "f" * 40, "--actor", ADVISOR)
    _show(f"[件② 候选非卡仓提交] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_CANDIDATE_INVALID" in err
    rc, out, err = _run_start(rig, "vr-hbx-60r-auditor-1")
    _show(f"---- 件② run-start(审计)exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"{base}/{AUDIT_CARD}/run-1" in out
    rc, _o, err = _run_start(rig, "vr-hbx-60-executor-1")
    _show(f"[件② 已有活动 run 再起] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_STATE_CONFLICT" in err

    def check(inst, task, path, run_id="run-1", *extra):
        argv = ["--instance", inst, "--task-id", task, "--host", HOST, "--check", path, "--level", "mutate"]
        if run_id:
            argv += ["--run-id", run_id]
        rc, out, _ = _wb(gov, *argv, *extra)
        return rc, out.strip()

    own = f"{base}/{CARD}/run-1/DerivedData/x.o"
    cases = [
        ("本卡本 run(执行)", (EXEC, CARD, own), 0, "ALLOW [OK]"),
        ("本卡本 run(审计)", (AUD, AUDIT_CARD, f"{base}/{AUDIT_CARD}/run-1/avd/x"), 0, "ALLOW [OK]"),
        ("他卡目录", (EXEC, CARD, f"{base}/{OTHER}/run-1/x"), 3, "DENY [NOT_OWN_TASK]"),
        ("他 run 目录", (EXEC, CARD, f"{base}/{CARD}/run-9/x", "run-9"), 3, "DENY [NOT_OWN_RUN]"),
        ("--run-id 与目录不符", (EXEC, CARD, f"{base}/{CARD}/run-2/x"), 3, "DENY [NOT_OWN_RUN]"),
        ("缺 --run-id", (EXEC, CARD, own, None), 3, "DENY [RUN_ID_REQUIRED]"),
        ("卡目录层(未进 run)", (EXEC, CARD, f"{base}/{CARD}"), 3, "DENY [OUTSIDE_TASK_RUN]"),
        ("base 层", (EXEC, CARD, f"{base}/notes.txt"), 3, "DENY [OUTSIDE_TASK_RUN]"),
        ("审计写执行 run(按审计卡)", (AUD, AUDIT_CARD, f"{base}/{CARD}/run-1/x"), 3, "DENY [NOT_OWN_TASK]"),
        ("审计冒执行卡号写执行 run", (AUD, CARD, f"{base}/{CARD}/run-1/x"), 3, "DENY [NOT_OWN_DECLARATION]"),
        ("执行写审计 run", (EXEC, AUDIT_CARD, f"{base}/{AUDIT_CARD}/run-1/x"), 3, "DENY [NOT_OWN_DECLARATION]"),
        ("越界(base 外)", (EXEC, CARD, f"{rig['mac']}/elsewhere/x"), 3, "DENY [SURFACE_UNDECLARED]"),
    ]
    for label, args, want_rc, needle in cases:
        rc, out = check(*args)
        _show(f"[件② {label}] exit {rc}: {out}")
        assert rc == want_rc and needle in out, (label, out)
    rc, out = check(EXEC, CARD, own)
    assert f"目标主机复核(只认退出码 0 + ALLOW): python3 validation_root_helper.py check --run-dir {base}/{CARD}/run-1" in out
    # 收口 → 已结束 run 拒; 新 run 号续铸(同 base/卡 不复用旧号)
    rc, out, err = _vr(gov, "run-finish", "--id", "vr-hbx-60-executor-1", "--run-id", "run-1", "--actor", ADVISOR, "--confirm")
    _show(f"---- 件② run-finish exit {rc} ----\n{out}{err}")
    assert rc == 0 and "收口" in out
    rc, out = check(EXEC, CARD, own)
    _show(f"[件② 已结束 run] exit {rc}: {out}")
    assert rc == 3 and "DENY [RUN_NOT_ACTIVE]" in out
    rc, out, err = _run_start(rig, "vr-hbx-60-executor-1")
    assert rc == 0 and f"{base}/{CARD}/run-2" in out, out + err
    rc, out = check(EXEC, CARD, f"{base}/{CARD}/run-2/x", "run-2")
    _show(f"[件② 新 run-2] exit {rc}: {out}")
    assert rc == 0 and "ALLOW [OK]" in out
    rc, out = check(EXEC, CARD, own)
    assert rc == 3 and "RUN_NOT_ACTIVE" in out


# ===========================================================================
# 件③ .. 穿越 / 符号链接逃逸(主机侧 helper + 本机 realpath)
# ===========================================================================

def test_item3_traversal_and_symlink_escape(rig):
    from tools.aipos_cli import write_boundary as wb
    from tools.aipos_cli.validation_root_helper import host_label

    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    run_dir = base / CARD / "run-1"
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", HOST, "--run-id", "run-1", "--level", "mutate",
                     "--check", f"{run_dir}/../../{OTHER}/run-1/x")
    _show(f"[件③ 治理根侧 .. 穿越] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [PATH_NOT_CANONICAL]" in out

    # 目标主机侧(靶场 = 临时目录模拟 Mac; helper 原文件子进程)
    outside = rig["tmp"] / "outside-secret"
    outside.mkdir()
    run_dir.mkdir(parents=True)
    (run_dir / "escape").symlink_to(outside)
    fp = _fp()
    for label, path, want, needle in (
        ("本 run 目录内", f"{run_dir}/results/a.xcresult", 0, "ALLOW [OK]"),
        ("符号链接逃逸", f"{run_dir}/escape/stolen.txt", 3, "DENY [SYMLINK_ESCAPE]"),
        (".. 穿越", f"{run_dir}/../run-2/x", 3, "DENY [PATH_NOT_CANONICAL]"),
        ("run 目录外", f"{base}/{OTHER}/run-1/x", 3, "DENY [OUTSIDE_RUN_DIR]"),
    ):
        rc, out = _helper("check", "--run-dir", str(run_dir), "--path", path, "--expect-fingerprint", fp)
        _show(f"[件③ helper {label}] exit {rc}: {out}")
        assert rc == want and needle in out, (label, out)
    linked_base = rig["tmp"] / "mac-link"
    linked_base.symlink_to(rig["mac"])
    rc, out = _helper("check", "--run-dir", f"{linked_base}/{CARD}/{CARD}/run-1", "--path", f"{linked_base}/{CARD}/{CARD}/run-1/x",
                      "--expect-fingerprint", fp)
    _show(f"[件③ helper 验证根路径自身含符号链接] exit {rc}: {out}")
    assert rc == 3 and "DENY [RUN_DIR_SYMLINK]" in out

    # 本机主机声明: 治理根侧以 realpath 判(同一 judge_realpath)
    local_base = rig["tmp"] / "local-vr" / CARD
    rc, out, err = _declare(rig, AUD, f"{OTHER}R", host=host_label(), base=local_base)
    _show(f"---- 件③ 本机主机声明(审计实例, 审计卡未派生)exit {rc} ----\n{out}{err}")
    assert rc == 0, out + err
    decl_id = "vr-hbx-61r-auditor-1"
    candidate = rig["candidate"]
    rc, out, err = _vr(gov, "run-start", "--id", decl_id, "--candidate", candidate, "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    local_run = local_base / f"{OTHER}R" / "run-1"
    local_run.mkdir(parents=True)
    (local_run / "escape").symlink_to(outside)
    ok = wb.check_access(gov, role="auditor", instance=AUD, task_id=f"{OTHER}R", run_id="run-1", level="mutate",
                         path=local_run / "logs" / "a.txt")
    bad = wb.check_access(gov, role="auditor", instance=AUD, task_id=f"{OTHER}R", run_id="run-1", level="mutate",
                          path=local_run / "escape" / "stolen.txt")
    _show(f"[件③ 本机 realpath] 本 run 内 → {ok['code']}: {ok['reason']}\n[件③ 本机 realpath] 符号链接逃逸 → {bad['code']}: {bad['reason']}")
    assert ok["allowed"] and ok["surface"] == "validation_root"
    assert not bad["allowed"] and bad["code"] == "SYMLINK_ESCAPE"


# ===========================================================================
# 件④ 撤销 / 到期 / 主机指纹不符
# ===========================================================================

def test_item4_revoke_expiry_fingerprint(rig, monkeypatch):
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli.validation_root_helper import host_fingerprint, host_label

    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    own = f"{base}/{CARD}/run-1/x"
    fake = host_fingerprint("another-machine-id")
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", HOST, "--run-id", "run-1", "--level", "mutate", "--check", own,
                     "--host-fingerprint", fake)
    _show(f"[件④ 调用方指纹不符] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [HOST_FINGERPRINT_MISMATCH]" in out
    rc, out = _helper("check", "--run-dir", f"{base}/{CARD}/run-1", "--path", own, "--expect-fingerprint", fake)
    _show(f"[件④ helper 本机指纹 ≠ 声明(跨机调用)] exit {rc}: {out}")
    assert rc == 3 and "DENY [HOST_FINGERPRINT_MISMATCH]" in out
    rc, out = _helper("fingerprint")
    _show(f"[件④ helper fingerprint] exit {rc}: {out}")
    assert rc == 0 and out.startswith(_fp()) and f"host={host_label()}" in out
    rc, _o, err = _run_start(rig, "vr-hbx-60r-auditor-1", "--host-fingerprint", fake)
    _show(f"[件④ run-start 指纹不符] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_HOST_FINGERPRINT_MISMATCH" in err
    rc, _o, err = _declare(rig, EXEC, OTHER, "--host-fingerprint", fake, host=host_label(), base=rig["tmp"] / "local-vr2" / OTHER,
                           confirm=False)
    _show(f"[件④ 本机声明给错指纹] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_HOST_FINGERPRINT_MISMATCH" in err

    # 到期: 时钟推过 expires_at(缺省 72 小时)
    real_now = vr._now()
    monkeypatch.setattr(vr, "_now", lambda: real_now + timedelta(hours=73))
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", HOST, "--run-id", "run-1", "--level", "mutate", "--check", own)
    _show(f"[件④ 到期后] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [DECLARATION_EXPIRED]" in out
    rc, _o, err = _run_start(rig, "vr-hbx-60r-auditor-1")
    assert rc == 1 and "VR_STATE_CONFLICT" in err and "expired" in err
    monkeypatch.setattr(vr, "_now", lambda: real_now)

    rc, out, err = _vr(gov, "revoke", "--id", "vr-hbx-60-executor-1", "--reason", "验证完成提前收回", "--actor", ADVISOR)
    _show(f"---- 件④ revoke --dry-run exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and '"revoke_reason": "验证完成提前收回"' in out
    rc, out, err = _vr(gov, "revoke", "--id", "vr-hbx-60-executor-1", "--reason", "验证完成提前收回", "--actor", ADVISOR, "--confirm")
    _show(f"---- 件④ revoke --confirm exit {rc} ----\n{out}{err}---- git log -1 ----\n{_git_log(gov)}")
    assert rc == 0 and "已写入" in out and "validation-root revoke" in _git_log(gov)
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", HOST, "--run-id", "run-1", "--level", "mutate", "--check", own)
    _show(f"[件④ 撤销后] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [DECLARATION_REVOKED]" in out
    rc, _o, err = _vr(gov, "revoke", "--id", "vr-hbx-60-executor-1", "--reason", "x", "--actor", ADVISOR, "--confirm")
    assert rc == 1 and "VR_STATE_CONFLICT" in err
    rc, _o, err = _vr(gov, "run-finish", "--id", "vr-nope", "--run-id", "run-1", "--actor", ADVISOR)
    assert rc == 1 and "VR_NOT_FOUND" in err
    rc, out, _ = _vr(gov, "list", "--task-id", CARD)
    _show(f"---- 件④ list(撤销后)----\n{out}")
    assert "[revoked]" in out and "revoked_by=" + ADVISOR in out


# ===========================================================================
# 件④⑤⑥ chris 形开工稿 / 章程同源节(Codex 沙箱可写根 + iOS / Android / Gradle 重定向)
# ===========================================================================

def test_item5_6_kickoff_and_charter_render(rig):
    gov, base = rig["gov"], rig["base"]
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    assert "跨机临时验证根" not in brief, "无声明 = 该节省略(行为不变)"
    _declare_both(rig)
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    _show(f"---- 件⑤ kickoff(声明后、未起 run)exit {rc} ----\n{brief}")
    assert rc == 0 and "当前无活动 run: 写入前须由顾问 / 驱动方铸 run" in brief
    assert f"project validation-root run-start --id vr-hbx-60-executor-1 --candidate <dev 候选 40 位 sha> --confirm" in brief
    assert _run_start(rig, "vr-hbx-60-executor-1")[0] == 0
    run_dir = f"{base}/{CARD}/run-1"
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    _show(f"---- 件⑤⑥ kickoff(chris 形, run-1 活动)exit {rc} ----\n{brief}")
    assert rc == 0, err
    from tools.aipos_cli.validation_root import helper_sha256

    for needle in (
        "## 跨机临时验证根(单源 project.json validation_roots; 判定 = write-boundary 面 validation_root, 与章程同源)",
        "与硬规矩衔接: 验证根是声明面, 不是硬规则例外",
        f"- 声明 vr-hbx-60-executor-1: 主机 {HOST}(指纹 {_fp()}), 卡 {CARD}, 实例 {EXEC}(executor)",
        f"当前活动 run: `run-1`(候选 {rig['candidate']}",
        f"本次只写 `{run_dir}`",
        f"roles write-boundary --instance {EXEC} --task-id {CARD} --host {HOST} --run-id run-1 --check <目标路径> --level mutate",
        f"python3 {run_dir}/.lybra-vr/validation_root_helper.py check --run-dir {run_dir} --path <目标> --expect-fingerprint {_fp()}",
        f"核 sha256 = {helper_sha256()}",
        f"子 agent 沙箱可写根(codex): codex exec --sandbox workspace-write --add-dir {run_dir}",
        f'writable_roots = ["{run_dir}"]',
        "禁 --sandbox danger-full-access",
        f"[ios] xcrun simctl --set {run_dir}/simdevices <子命令>",
        f"-derivedDataPath {run_dir}/DerivedData -resultBundlePath {run_dir}/results/<名>.xcresult",
        f"[android] ANDROID_USER_HOME={run_dir}/android-user-home", f"[android] ANDROID_AVD_HOME={run_dir}/avd",
        f"[android] ANDROID_EMULATOR_HOME={run_dir}/android-user-home",
        f"[gradle] GRADLE_USER_HOME={run_dir}/gradle-home", "[gradle] GRADLE_RO_DEP_CACHE=<目标主机既有依赖缓存",
        "系统服务副作用 `~/Library/Logs/CoreSimulator`: CoreSimulator 系统服务自身日志(系统服务副作用, 非 agent 写入)",
        "BLOCK / 禁止: 真实设备", "收口: 本次验证结束由顾问 / 驱动方",
        f"- validation_root: mutate `<声明 base>/{CARD}/<run_id>(按主机声明: lybra project validation-root list)`(本卡本活动 run 目录(按主机))",
    ):
        assert needle in brief, f"开工稿缺 {needle!r}"
    assert f"{base}/{AUDIT_CARD}" not in brief, "执行开工稿不列审计声明(执行与审计隔离)"

    rc, charter, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", gov)
    section = charter[charter.index("## 🔴 写权限边界"):]
    _show(f"---- 件⑤ 执行者章程写权限节(chris 形 Codex 子 agent)exit {rc} ----\n{section}")
    assert rc == 0, err
    assert "| validation_root | **mutate** |" in section and "## 跨机临时验证根" in section
    assert f"codex exec --sandbox workspace-write --add-dir {run_dir}" in section and f"{base}/{AUDIT_CARD}" not in section
    rc, charter, err = _cli("charter", "--role", "auditor", "--instance", AUD, "--workspace-root", gov)
    section = charter[charter.index("## 🔴 写权限边界"):] if rc == 0 else ""
    _show(f"---- 件⑤ 审计者章程写权限节 exit {rc} ----\n{section or err}")
    assert rc == 0 and f"- 声明 vr-hbx-60r-auditor-1: 主机 {HOST}" in section and f"{base}/{AUDIT_CARD}/<run-id>/" in section
    assert "vr-hbx-60-executor-1" not in section


# ===========================================================================
# 运行证据对接 / 单源 / 发现
# ===========================================================================

def test_runtime_evidence_refs_executor_run_dir(rig):
    from tools.aipos_cli.validation_root import executor_run_dir_refs

    _declare_both(rig)
    base = rig["base"]
    hits = executor_run_dir_refs(rig["gov"], CARD, [f"xcodebuild test -resultBundlePath {base}/{CARD}/run-1/results/r.xcresult"])
    clean = executor_run_dir_refs(rig["gov"], CARD, [f"xcodebuild test -resultBundlePath {base}/{AUDIT_CARD}/run-1/results/r.xcresult"])
    _show(f"[件② 运行证据对接] 引用执行 run → {hits}; 审计自己的 run → {clean}")
    assert hits == [f"{base}/{CARD}/"] and clean == []
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "runtime_evidence_check")
    assert "executor_run_dir_refs" in ast.unparse(fn), "运行证据判据(唯一实现)调用验证根对接"


def test_single_source_and_helper_is_stdlib_only(tmp_path):
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli import write_boundary as wb

    decl = wb.load_write_boundary_declaration()
    assert decl["surfaces"]["validation_root"]["root"] == "validation_roots"
    assert decl["matrix"]["executor"]["validation_root"] == "mutate" and decl["matrix"]["auditor"]["validation_root"] == "mutate"
    assert set(vr.declaration()["check_codes"]) >= {"PATH_NOT_CANONICAL", "SYMLINK_ESCAPE", "NOT_OWN_RUN", "DECLARATION_REVOKED"}
    # 判定唯一入口: check_access 调 judge_access; 主机身份只在 helper
    wb_src = (REPO_ROOT / "tools" / "aipos_cli" / "write_boundary.py").read_text(encoding="utf-8")
    assert "vr.judge_access(" in wb_src
    vr_src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    assert "def host_fingerprint" not in vr_src and "def judge_realpath" not in vr_src and "machine-id" not in vr_src
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    _show(f"[单源] helper imports = {sorted(imported)}")
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
    rc, out, _ = _cli("project", "validation-root", "helper", "--sha256")
    assert rc == 0 and out.strip() == vr.helper_sha256()
    rc, out, _ = _cli("project", "validation-root", "helper")
    assert rc == 0 and out == HELPER.read_text(encoding="utf-8")
    copy = tmp_path / "copied_helper.py"  # 目标主机上拷贝运行(脱离产品仓)
    copy.write_text(out, encoding="utf-8")
    proc = subprocess.run([sys.executable, "-I", str(copy), "fingerprint"], capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    assert proc.returncode == 0 and proc.stdout.startswith("hfp1:")


def test_helper_snapshot_compare(tmp_path):
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    watched = tmp_path / "Logs" / "CoreSimulator"
    watched.mkdir(parents=True)
    (watched / "a.log").write_text("x", encoding="utf-8")
    rc, out = _helper("snapshot", "--run-dir", str(run_dir), "--out", f"{run_dir}/side/before.json", str(watched))
    assert rc == 0, out
    (watched / "b.log").write_text("y", encoding="utf-8")
    rc, out = _helper("snapshot", "--run-dir", str(run_dir), "--out", f"{run_dir}/side/after.json", str(watched))
    assert rc == 0, out
    rc, out = _helper("compare", f"{run_dir}/side/before.json", f"{run_dir}/side/after.json")
    _show(f"[件⑤ 系统服务副作用前后清单] exit {rc}:\n{out}")
    assert rc == 4 and f"+ {watched}/b.log" in out and "CHANGED 1 处" in out
    rc, out = _helper("snapshot", "--run-dir", str(run_dir), "--out", f"{tmp_path}/outside.json", str(watched))
    assert rc == 3 and "DENY [OUTSIDE_RUN_DIR]" in out
    assert os.path.exists(f"{run_dir}/side/after.json") and not os.path.exists(f"{tmp_path}/outside.json")


def test_f190_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f190_validation_root.py", default_test_contract())
