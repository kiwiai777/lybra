"""AIPOS-F194 — iOS 界面测试隔离改为「本次运行专用模拟器」(Owner 10-20 定; chris 理想 HBLX-21B 实撞)。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi / 真 Mac; 治理根所在机无 xcrun):
  - 治理根侧 = F190 chris 形靶场(同一 rig: 临时治理根 + Codex 子 agent 执行者在他机 sim-mac + 本机审计工位);
  - 目标主机侧 = 临时目录模拟 Mac 用户主目录(HOME 指向它, Devices = $HOME/Library/Developer/CoreSimulator/Devices),
    `xcrun` 桩 = 可执行脚本(PATH 前置)模拟 `simctl create`(建 UDID 目录 + device.plist + device_set.plist, stdout 打 UDID)与
    `simctl delete <UDID>`; 桩模式: extra(建台时多出他台)/ wrong(删错他台)/ noop(未删除); `getconf` 桩给 Darwin 用户临时目录。
  - helper 原文件起子进程(与目标主机同一份)。

验收原文全部经 _show 打到 stdout(RETURN 证据): ①sim-create / verify / delete 正例与「多出他台 / 删错他台 / 未删除」拒;
②开工稿 iOS 动作模板(chris 形); ③write-boundary 本 run UDID 允许、他 run / 审计复用执行 UDID 拒; ④系统临时目录副作用快照;
⑤roles.schema 撤下 --set 方案(单源断言); run-finish / sim-bind 证据核对与拒。
"""
from __future__ import annotations

import ast
import copy
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f190_validation_root import (  # noqa: E402,F401  — 靶场唯一来源(F190 chris 形 rig)
    ADVISOR, AUD, AUDIT_CARD, CARD, EXEC, HELPER, HOST, _cli, _declare_both, _fp, _run_start, _show, _vr, _wb, rig,
)

EXEC_DECL, AUD_DECL = "vr-hbx-60-executor-1", "vr-hbx-60r-auditor-1"
DEVICES_REL = "Library/Developer/CoreSimulator/Devices"
UDID_RE = re.compile(r"SIM_CREATED udid=([0-9A-F-]{36})")

XCRUN_STUB = r'''
import json, os, plistlib, shutil, sys, uuid
args = sys.argv[1:]
log = os.environ.get("SIMCTL_STUB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as h:
        h.write(json.dumps(args) + "\n")
if args[:1] != ["simctl"] or len(args) < 2:
    sys.stderr.write("xcrun stub: only simctl\n"); sys.exit(64)
ddir = os.path.join(os.environ["HOME"], "Library/Developer/CoreSimulator/Devices")
mode = os.environ.get("SIMCTL_STUB_MODE", "")
def touch_set():
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, "device_set.plist"), "ab") as h:
        h.write(b"#")
def mk(name, dtype="t", runtime="r"):
    udid = str(uuid.uuid4()).upper()
    os.makedirs(os.path.join(ddir, udid, "data"))
    with open(os.path.join(ddir, udid, "device.plist"), "wb") as h:
        plistlib.dump({"UDID": udid, "name": name, "deviceType": dtype, "runtime": runtime, "state": 1}, h)
    touch_set()
    return udid
if args[1] == "create" and len(args) == 5:
    udid = mk(args[2], args[3], args[4])
    if mode == "extra":
        mk("someone-else")
    print(udid); sys.exit(0)
if args[1] == "delete" and len(args) == 3:
    if mode == "noop":
        sys.exit(0)
    target = args[2]
    if mode == "wrong":
        target = sorted(n for n in os.listdir(ddir) if n != args[2] and len(n) == 36)[0]
    shutil.rmtree(os.path.join(ddir, target)); touch_set(); sys.exit(0)
sys.stderr.write("xcrun stub: unsupported " + " ".join(args) + "\n"); sys.exit(64)
'''


def _stub_bin(tmp: Path, temp_dir: Path | None = None) -> Path:
    bindir = tmp / "stubbin"
    bindir.mkdir(exist_ok=True)
    xcrun = bindir / "xcrun"
    xcrun.write_text(f"#!{sys.executable}\n" + XCRUN_STUB, encoding="utf-8")
    xcrun.chmod(0o755)
    if temp_dir is not None:
        getconf = bindir / "getconf"
        getconf.write_text(f"#!/bin/sh\n[ \"$1\" = DARWIN_USER_TEMP_DIR ] && echo '{temp_dir}/' && exit 0\nexit 1\n", encoding="utf-8")
        getconf.chmod(0o755)
    return bindir


def _mac_home(r: dict) -> Path:
    home = r["tmp"] / "machome"
    (home / DEVICES_REL).mkdir(parents=True, exist_ok=True)
    return home


def _seed_device(home: Path, name: str) -> str:
    """他人既有设备(非本 run)。"""
    import uuid

    udid = str(uuid.uuid4()).upper()
    d = home / DEVICES_REL / udid
    (d / "data").mkdir(parents=True)
    with open(d / "device.plist", "wb") as h:
        plistlib.dump({"UDID": udid, "name": name}, h)
    return udid


def _host(r: dict, *argv: str, mode: str = "", temp_dir: Path | None = None) -> tuple[int, str]:
    """模拟目标主机运行 helper 原文件(HOME = 模拟 Mac 主目录, PATH 前置 xcrun 桩)。"""
    home = _mac_home(r)
    env = {**os.environ, "HOME": str(home), "PATH": f"{_stub_bin(r['tmp'], temp_dir)}:{os.environ.get('PATH', '')}",
           "SIMCTL_STUB_MODE": mode, "SIMCTL_STUB_LOG": str(r["tmp"] / "simctl-calls.jsonl")}
    proc = subprocess.run([sys.executable, str(HELPER), *argv], capture_output=True, text=True, timeout=120, env=env)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _run_dir(r: dict, card: str = CARD, run_id: str = "run-1") -> Path:
    d = r["base"] / card / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _devices(r: dict) -> list[str]:
    return sorted(n for n in os.listdir(_mac_home(r) / DEVICES_REL) if len(n) == 36)


def _create(r: dict, run_dir: Path, mode: str = "") -> tuple[int, str, str | None]:
    rc, out = _host(r, "sim-create", "--run-dir", str(run_dir), "--expect-fingerprint", _fp(), "--device-type",
                    "com.apple.CoreSimulator.SimDeviceType.iPhone-15", "--runtime", "com.apple.CoreSimulator.SimRuntime.iOS-17-5", mode=mode)
    m = UDID_RE.search(out)
    return rc, out, m.group(1) if m else None


# ===========================================================================
# 验收① 主机侧 sim-create / sim-verify / sim-delete 正例
# ===========================================================================

def test_item1_sim_create_verify_delete_happy(rig):
    from tools.aipos_cli.validation_root_helper import verify_sim_evidence

    home = _mac_home(rig)
    others = sorted([_seed_device(home, "iPhone 15(他人既有)"), _seed_device(home, "lybra-HBX-61-run-1(他卡)")])
    run_dir = _run_dir(rig)
    rc, out, udid = _create(rig, run_dir)
    _show(f"---- 验收① sim-create(桩)exit {rc} ----\n{out}")
    assert rc == 0 and udid and f"SIM_CREATED udid={udid} name=lybra-{CARD}-run-1" in out
    assert "service-side-effect 建台 ~ device_set.plist" in out or "service-side-effect 建台 + device_set.plist" in out
    assert _devices(rig) == sorted(others + [udid])
    rc, out = _host(rig, "sim-verify", "--run-dir", str(run_dir), "--expect-fingerprint", _fp())
    _show(f"---- 验收① sim-verify exit {rc} ----\n{out}")
    assert rc == 0 and f"SIM_OK udid={udid}" in out
    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_dir), "--expect-fingerprint", _fp())
    _show(f"---- 验收① sim-delete exit {rc} ----\n{out}")
    assert rc == 0 and f"SIM_DELETED udid={udid}" in out and "只多出并只删除这一台" in out
    assert _devices(rig) == others, "他台未动"
    calls = [json.loads(ln) for ln in (rig["tmp"] / "simctl-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    _show(f"[验收① simctl 调用全集] {calls}")
    assert calls == [["simctl", "create", f"lybra-{CARD}-run-1", "com.apple.CoreSimulator.SimDeviceType.iPhone-15",
                      "com.apple.CoreSimulator.SimRuntime.iOS-17-5"], ["simctl", "delete", udid]], "只调 create 与 delete 本 run UDID"
    evidence = json.loads((run_dir / ".lybra-vr" / "simulator.json").read_text(encoding="utf-8"))
    verdict = verify_sim_evidence(evidence, run_dir=str(run_dir), expect_udid=udid)
    _show(f"[验收① 证据核对] ok={verdict['ok']} service={verdict['service']}")
    assert verdict["ok"] and evidence["create"]["before"]["udids"] == others and evidence["delete"]["after"]["udids"] == others


# ===========================================================================
# 验收① 拒: 多出他台 / 删错他台 / 未删除 / 重建 / 删他台(名不符)/ 运行中他台变化 / 跨机
# ===========================================================================

def test_item1_sim_refusals(rig):
    home = _mac_home(rig)
    other = _seed_device(home, "iPhone 15(他人既有)")
    # 多出他台
    run_a = _run_dir(rig, CARD, "run-a")
    rc, out, udid_a = _create(rig, run_a, mode="extra")
    _show(f"[验收① 拒 多出他台] exit {rc}:\n{out}")
    assert rc == 3 and "DENY [SIM_CREATE_UNVERIFIED]" in out and "多出他台 = 拒" in out and udid_a is None
    rc, out = _host(rig, "sim-create", "--run-dir", str(run_a), "--expect-fingerprint", _fp(), "--device-type", "t", "--runtime", "r")
    _show(f"[验收① 拒 同 run 重建] exit {rc}: {out}")
    assert rc == 3 and "DENY [SIM_ALREADY_CREATED]" in out
    # 删错他台
    run_b = _run_dir(rig, CARD, "run-b")
    rc, out, udid_b = _create(rig, run_b)
    assert rc == 0 and udid_b, out
    before = set(_devices(rig))
    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_b), "--expect-fingerprint", _fp(), mode="wrong")
    _show(f"[验收① 拒 删错他台(桩删了别的 UDID)] exit {rc}:\n{out}")
    assert rc == 3 and "DENY [SIM_DELETE_UNVERIFIED]" in out and "删错他台 / 未删除 = 拒" in out
    assert udid_b in set(_devices(rig)) and len(before - set(_devices(rig))) == 1
    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_b), "--expect-fingerprint", _fp())
    _show(f"[验收① 拒 重复删除] exit {rc}: {out}")
    assert rc == 3 and "DENY [SIM_ALREADY_DELETED]" in out
    # 未删除
    run_c = _run_dir(rig, CARD, "run-c")
    rc, out, udid_c = _create(rig, run_c)
    assert rc == 0 and udid_c, out
    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_c), "--expect-fingerprint", _fp(), mode="noop")
    _show(f"[验收① 拒 未删除(桩 delete 无动作)] exit {rc}:\n{out}")
    assert rc == 3 and "DENY [SIM_DELETE_UNVERIFIED]" in out and "少了的设备 = (无)" in out
    # 运行中他台变化
    run_d = _run_dir(rig, CARD, "run-d")
    rc, out, udid_d = _create(rig, run_d)
    assert rc == 0 and udid_d, out
    intruder = _seed_device(home, "运行中他人新建")
    rc, out = _host(rig, "sim-verify", "--run-dir", str(run_d), "--expect-fingerprint", _fp())
    _show(f"[验收① 拒 运行中多出他台] exit {rc}:\n{out}")
    assert rc == 3 and "DENY [SIM_SET_CHANGED]" in out and intruder in out
    # 证据被改指向他台 = 名不符不删(防删错他台)
    run_e = _run_dir(rig, CARD, "run-e")
    rc, out, udid_e = _create(rig, run_e)
    assert rc == 0 and udid_e, out
    path = run_e / ".lybra-vr" / "simulator.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    other = _seed_device(home, "iPhone 15 Pro(他人既有)")  # 前面「删错他台」桩可能已删掉先前的他台, 另建一台
    tampered["udid"] = other
    path.write_text(json.dumps(tampered), encoding="utf-8")
    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_e), "--expect-fingerprint", _fp())
    _show(f"[验收① 拒 证据被改指向他台] exit {rc}: {out}")
    assert rc == 3 and "DENY [SIM_NOT_OWN]" in out and other in _devices(rig)
    # 跨机 / run 目录缺
    from tools.aipos_cli.validation_root_helper import host_fingerprint

    rc, out = _host(rig, "sim-create", "--run-dir", str(run_e), "--expect-fingerprint", host_fingerprint("another-mac"), "--device-type", "t",
                    "--runtime", "r")
    _show(f"[验收① 拒 跨机] exit {rc}: {out}")
    assert rc == 3 and "HOST_FINGERPRINT_MISMATCH" in out
    rc, out = _host(rig, "sim-create", "--run-dir", str(rig["base"] / CARD / "run-zz"), "--expect-fingerprint", _fp(), "--device-type", "t",
                    "--runtime", "r")
    _show(f"[验收① 拒 run 目录未初始化] exit {rc}: {out}")
    assert rc == 3 and "RUN_DIR_MISSING" in out
    calls = [json.loads(ln) for ln in (rig["tmp"] / "simctl-calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(c[1] in ("create", "delete") for c in calls) and not any(c[1:3] == ["delete", "all"] for c in calls)


# ===========================================================================
# 治理根侧: sim-bind / run-finish --sim-evidence 核对(UDID 与 run-id 同一处)
# ===========================================================================

def _project_runs(gov: Path, decl_id: str) -> list[dict]:
    from tools.aipos_cli.validation_root import project_validation_roots

    return next(e for e in project_validation_roots(gov) if e["id"] == decl_id)["runs"]


def test_bind_and_finish_evidence(rig):
    gov = rig["gov"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0
    run_dir = _run_dir(rig)
    rc, out, udid = _create(rig, run_dir)
    assert rc == 0 and udid, out
    evidence = run_dir / ".lybra-vr" / "simulator.json"
    copy_dir = rig["tmp"] / "dev-copy"  # 治理根所在机上的证据副本(真实流程 = 复制进本卡报告落点 artifacts/)
    copy_dir.mkdir()

    rc, _o, err = _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", "not-a-udid", "--actor", ADVISOR)
    _show(f"[bind 拒 UDID 形] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_SIM_INVALID" in err
    before = (gov / "project.json").read_text(encoding="utf-8")
    rc, out, err = _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", udid, "--sim-evidence", evidence, "--actor", ADVISOR)
    _show(f"---- sim-bind --dry-run exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and f'"udid": "{udid}"' in out and (gov / "project.json").read_text(encoding="utf-8") == before
    rc, out, err = _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", udid, "--actor", ADVISOR, "--confirm")
    log = rig_log(gov)
    _show(f"---- sim-bind --confirm exit {rc} ----\n{out}{err}---- git log -1 ----\n{log}")
    assert rc == 0 and "已写入" in out and f"专用模拟器 lybra-{CARD}-run-1 udid={udid}" in out and "validation-root sim-bind" in log
    run = _project_runs(gov, EXEC_DECL)[0]
    assert run["run_id"] == "run-1" and run["simulator"]["udid"] == udid and run["simulator"]["verified_at"] is None
    rc, _o, err = _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", udid, "--actor", ADVISOR)
    assert rc == 1 and "VR_SIM_INVALID" in err and "已绑定" in err

    rc, _o, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--actor", ADVISOR, "--confirm")
    _show(f"[run-finish 拒 绑定了却无证据] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_SIM_EVIDENCE_REJECTED" in err and "--sim-evidence" in err
    (copy_dir / "not-deleted.json").write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    rc, _o, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--sim-evidence", copy_dir / "not-deleted.json",
                      "--actor", ADVISOR, "--confirm")
    _show(f"[run-finish 拒 未删除] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_SIM_EVIDENCE_REJECTED" in err and "未删除" in err

    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_dir), "--expect-fingerprint", _fp())
    assert rc == 0, out
    good = json.loads(evidence.read_text(encoding="utf-8"))
    other = "11111111-2222-3333-4444-555555555555"
    variants = {}
    v = copy.deepcopy(good)
    v["create"]["after"]["udids"] = sorted(v["create"]["after"]["udids"] + [other])
    variants["多出他台"] = (v, "多出他台 = 拒")
    v = copy.deepcopy(good)
    v["delete"]["before"]["udids"] = sorted(v["delete"]["before"]["udids"] + [other])
    v["delete"]["after"]["udids"] = sorted(set(v["delete"]["before"]["udids"]) - {other})
    variants["删错他台"] = (v, "删错他台 / 未删除 = 拒")
    v = copy.deepcopy(good)
    v["run_dir"] = str(rig["base"] / CARD / "run-9")
    variants["他 run 证据"] = (v, "他 run 的证据")
    for label, (data, needle) in variants.items():
        p = copy_dir / f"{label}.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rc, _o, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--sim-evidence", p, "--actor", ADVISOR, "--confirm")
        _show(f"[run-finish 拒 {label}] exit {rc}: {err.strip()}")
        assert rc == 1 and "VR_SIM_EVIDENCE_REJECTED" in err and needle in err, (label, err)
    assert _project_runs(gov, EXEC_DECL)[0]["finished_at"] is None, "拒 = 零写入"
    (copy_dir / "simulator.json").write_text(evidence.read_text(encoding="utf-8"), encoding="utf-8")
    rc, out, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--sim-evidence", copy_dir / "simulator.json",
                       "--actor", ADVISOR, "--confirm")
    _show(f"---- run-finish --sim-evidence --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "专用模拟器证据核对通过: 只多出并只删除" in out
    run = _project_runs(gov, EXEC_DECL)[0]
    assert run["finished_at"] and run["simulator"]["verified_at"] and len(run["simulator"]["evidence_sha256"]) == 64
    rc, out, _ = _vr(gov, "list", "--task-id", CARD)
    _show(f"---- list ----\n{out}")
    assert f"专用模拟器 lybra-{CARD}-run-1 udid={udid}" in out and "删除核对通过" in out


def rig_log(gov: Path) -> str:
    import test_aipos_f78_engine_agnostic as f78

    return f78._git(gov, "log", "-1", "--format=%s%n%b", "--name-only")


# ===========================================================================
# 验收③ write-boundary: 本 run UDID 允许; 他 run / 他卡 / 审计复用执行 UDID / 未绑定 / 整个 Devices 拒
# ===========================================================================

def test_item3_write_boundary_and_audit_reuse(rig):
    from tools.aipos_cli.validation_root import executor_run_dir_refs

    gov = rig["gov"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0 and _run_start(rig, AUD_DECL)[0] == 0
    other = _seed_device(_mac_home(rig), "iPhone 15(他人既有)")  # 建台前已存在的他台
    run_dir = _run_dir(rig)
    rc, out, udid = _create(rig, run_dir)
    assert rc == 0 and udid, out
    assert _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", udid, "--actor", ADVISOR, "--confirm")[0] == 0
    rc, _o, err = _vr(gov, "sim-bind", "--id", AUD_DECL, "--run-id", "run-1", "--udid", udid, "--actor", ADVISOR, "--confirm")
    _show(f"[验收③ 审计 run 复用执行 UDID 绑定] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_SIM_INVALID" in err and "审计" in err and "不得复用他 run 的 UDID" in err

    mac_devices = f"/Users/kiwi/{DEVICES_REL}"  # 目标主机(远端)路径: 治理根侧字面判定

    def check(inst, task, path, run_id="run-1"):
        rc, out, _ = _wb(gov, "--instance", inst, "--task-id", task, "--host", HOST, "--run-id", run_id, "--check", path, "--level", "mutate")
        return rc, out.strip()

    own = f"{mac_devices}/{udid}/data/Containers/x.plist"
    cases = [
        ("本 run UDID(执行)", (EXEC, CARD, own), 0, "ALLOW [OK]"),
        ("审计复用执行 UDID", (AUD, AUDIT_CARD, own), 3, "DENY [SIM_NOT_OWN_RUN]"),
        ("他 run 号", (EXEC, CARD, own, "run-2"), 3, "DENY [SIM_NOT_OWN_RUN]"),
        ("他卡", (EXEC, "HBX-61", own), 3, "DENY [SIM_NOT_OWN_RUN]"),
        ("未绑定 UDID(他人既有设备)", (EXEC, CARD, f"{mac_devices}/AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE/data/x"), 3, "DENY [SIM_NOT_BOUND]"),
        ("整个 Devices 目录", (EXEC, CARD, mac_devices), 3, "DENY [SIM_OUTSIDE_DEVICE]"),
        ("device_set.plist(服务维护项)", (EXEC, CARD, f"{mac_devices}/device_set.plist"), 3, "DENY [SIM_OUTSIDE_DEVICE]"),
        (".. 穿越到他台", (EXEC, CARD, f"{mac_devices}/{udid}/../AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE/x"), 3, "DENY [PATH_NOT_CANONICAL]"),
        ("Xcode 全局缓存", (EXEC, CARD, "/Users/kiwi/Library/Developer/Xcode/DerivedData/x"), 3, "DENY [SURFACE_UNDECLARED]"),
    ]
    for label, args, want, needle in cases:
        rc, out = check(*args)
        _show(f"[验收③ {label}] exit {rc}: {out}")
        assert rc == want and needle in out, (label, out)
    rc, out = check(EXEC, CARD, own)
    assert f"python3 validation_root_helper.py check --run-dir {run_dir} --sim-udid {udid}" in out

    # 目标主机侧 helper check --sim-udid(realpath + device.plist 名)
    home = _mac_home(rig)
    device = home / DEVICES_REL / udid
    outside = rig["tmp"] / "outside"
    outside.mkdir()
    (device / "data" / "escape").symlink_to(outside)
    for label, args, want, needle in (
        ("本 run 设备内", (udid, f"{device}/data/x"), 0, "ALLOW [OK]"),
        ("符号链接逃逸", (udid, f"{device}/data/escape/x"), 3, "DENY [SYMLINK_ESCAPE]"),
        ("他台设备(名不符)", (other, f"{home / DEVICES_REL / other}/data/x"), 3, "DENY [SIM_NOT_OWN_RUN]"),
        ("设备目录外", (udid, f"{home / DEVICES_REL}/device_set.plist"), 3, "DENY [OUTSIDE_RUN_DIR]"),
    ):
        rc, out = _host(rig, "check", "--run-dir", str(run_dir), "--sim-udid", args[0], "--path", args[1], "--expect-fingerprint", _fp())
        _show(f"[验收③ helper check --sim-udid {label}] exit {rc}: {out}")
        assert rc == want and needle in out, (label, out)

    hits = executor_run_dir_refs(gov, CARD, [f"xcodebuild test-without-building -destination 'platform=iOS Simulator,id={udid}'"])
    clean = executor_run_dir_refs(gov, CARD, ["xcodebuild test-without-building -destination 'platform=iOS Simulator,id="
                                              "99999999-8888-7777-6666-555555555555'"])
    _show(f"[验收③ 运行证据: 审计引用执行 UDID] → {hits}; 审计自己的 UDID → {clean}")
    assert hits == [f"专用模拟器 {udid}(执行 {EXEC_DECL} run-1)"] and clean == []

    rc, out = _host(rig, "sim-delete", "--run-dir", str(run_dir), "--expect-fingerprint", _fp())
    assert rc == 0, out
    rc, _o, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--sim-evidence", run_dir / ".lybra-vr" / "simulator.json",
                      "--actor", ADVISOR, "--confirm")
    assert rc == 0, err
    rc, out = check(EXEC, CARD, own)
    _show(f"[验收③ run 收口后] exit {rc}: {out}")
    assert rc == 3 and "DENY [RUN_NOT_ACTIVE]" in out


# ===========================================================================
# 验收④ 系统临时目录副作用快照(Xcode 自写 ResultBundle_*)
# ===========================================================================

def test_item4_sys_side_effect_snapshot(rig):
    run_dir = _run_dir(rig)
    temp = rig["tmp"] / "var-folders" / "3q" / "r3tt" / "T"
    temp.mkdir(parents=True)
    (temp / "ResultBundle_2026-19-10_10-00-0000.xcresult").mkdir()
    (temp / "unrelated.tmp").write_text("x", encoding="utf-8")
    common = ["--run-dir", str(run_dir), "--expect-fingerprint", _fp(), "--pattern", "ResultBundle_*"]
    rc, out = _host(rig, "sys-snapshot", *common, "--phase", "after", temp_dir=temp)
    assert rc == 3 and "SIDE_BEFORE_MISSING" in out
    rc, out = _host(rig, "sys-snapshot", *common, "--phase", "before", temp_dir=temp)
    _show(f"[验收④ 前快照(getconf 桩给 Darwin 用户临时目录)] exit {rc}: {out}")
    assert rc == 0 and "SIDE_BEFORE 1 项" in out and f"temp_dir={temp}" in out
    rc, out = _host(rig, "sys-snapshot", *common, "--phase", "before", temp_dir=temp)
    assert rc == 3 and "SIDE_BEFORE_EXISTS" in out
    (temp / "ResultBundle_2026-20-10_00-53-0042.xcresult").mkdir()  # Xcode 自写(chris HBLX-21B 实证同名形)
    (temp / "other-new.tmp").write_text("y", encoding="utf-8")
    rc, out = _host(rig, "sys-snapshot", *common, "--phase", "after", temp_dir=temp)
    _show(f"[验收④ 后快照] exit {rc}:\n{out}")
    assert rc == 0 and f"SIDE_EFFECT + {temp}/ResultBundle_2026-20-10_00-53-0042.xcresult" in out and "SIDE_EFFECTS 1 处" in out
    assert "other-new.tmp" not in out and (temp / "ResultBundle_2026-20-10_00-53-0042.xcresult").is_dir(), "不删除"
    data = json.loads((run_dir / ".lybra-vr" / "xcode-side-effects.json").read_text(encoding="utf-8"))
    assert data["added"] == ["ResultBundle_2026-20-10_00-53-0042.xcresult"] and data["temp_dir"] == str(temp)
    rc, out = _host(rig, "sys-snapshot", "--run-dir", str(run_dir), "--expect-fingerprint", _fp(), "--phase", "after", "--pattern", "*",
                    "--temp-dir", str(temp))
    assert rc == 3 and "SIDE_SCOPE_MISMATCH" in out


# ===========================================================================
# 验收② 开工稿 / 章程同源: iOS 动作模板(chris 形)
# ===========================================================================

def test_item2_kickoff_and_charter_ios_template(rig):
    gov = rig["gov"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0
    run_dir = f"{rig['base']}/{CARD}/run-1"
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0, err
    assert f"-destination 'platform=iOS Simulator,id=<本 run UDID(sim-create 末行 SIM_CREATED udid=…)>'" in brief
    rdir = _run_dir(rig)
    rc, out, udid = _create(rig, rdir)
    assert rc == 0 and udid, out
    assert _vr(gov, "sim-bind", "--id", EXEC_DECL, "--run-id", "run-1", "--udid", udid, "--actor", ADVISOR, "--confirm")[0] == 0
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    section = brief[brief.index("  iOS 本 run 专用模拟器"):brief.index("  收口: ")]
    _show(f"---- 验收② kickoff iOS 动作模板(chris 形, run-1 已绑定 {udid})exit {rc} ----\n{section}")
    assert rc == 0, err
    helper = f"python3 {run_dir}/.lybra-vr/validation_root_helper.py"
    common = f"--run-dir {run_dir} --expect-fingerprint {_fp()}"
    for needle in (
        f"本 run 名 `lybra-{CARD}-run-1`, 只写 `~/{DEVICES_REL}/{udid}/` 这一台",
        "不用 `simctl --set` 独立设备集——已撤下",
        f"{helper} sys-snapshot {common} --phase before --pattern 'ResultBundle_*'",
        f"{helper} sim-create {common} --device-type <设备型号 id> --runtime <运行时 id>",
        f"project validation-root sim-bind --id {EXEC_DECL} --run-id run-1 --udid {udid} --confirm",
        f"`xcodebuild build-for-testing <-project|-workspace …> -scheme <scheme> -destination 'platform=iOS Simulator,id={udid}' "
        f"-derivedDataPath {run_dir}/DerivedData -resultBundlePath {run_dir}/results/build-for-testing.xcresult`",
        f"`xcodebuild test-without-building <-project|-workspace …> -scheme <scheme> -destination 'platform=iOS Simulator,id={udid}' "
        f"-derivedDataPath {run_dir}/DerivedData -resultBundlePath {run_dir}/results/test-without-building.xcresult`",
        f"{helper} sim-verify {common}", f"{helper} sim-delete {common}",
        f"{helper} sys-snapshot {common} --phase after --pattern 'ResultBundle_*'",
        f"project validation-root run-finish --id {EXEC_DECL} --run-id run-1 --sim-evidence <simulator.json 在治理根所在机的副本>",
    ):
        assert needle in section, f"开工稿 iOS 段缺 {needle!r}"
    assert "simdevices" not in brief and "xcrun simctl --set" not in brief
    rc, charter, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", gov)
    assert rc == 0, err
    # AIPOS-F206: 章程只留通用规则(iOS 只用本 run 专用模拟器), 本 run UDID / helper 命令只在开工稿(上文已核)
    assert "iOS 界面测试只用本 run 专用模拟器(helper sim-create 新建 / sim-delete 删除, 一 run 一台)" in charter
    assert udid not in charter and f"{helper} sim-delete {common}" not in charter, "章程不列本 run 明细"


# ===========================================================================
# 验收⑤ 单源: roles.schema 撤下 --set; helper 常量 = 声明; helper 纯标准库; 核对函数唯一
# ===========================================================================

def test_item5_schema_retracts_set_and_single_source():
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli import validation_root_helper as helper

    decl = vr.declaration()
    redirects = json.dumps(decl["tool_redirects"], ensure_ascii=False)
    assert "--set" not in redirects and "simdevices" not in redirects
    assert "--set" not in json.dumps(decl["block_conditions"], ensure_ascii=False)
    sim = vr.ios_simulator_declaration()
    _show(f"[验收⑤ 撤下记录] {json.dumps(sim['retracted'], ensure_ascii=False)}")
    assert "IDECustomDeviceSetPath" in sim["retracted"]["reason"] and sim["retracted"]["retracted_at"] == "2026-10-20"
    assert sim["name_template"] == helper.SIM_NAME_TEMPLATE and sim["devices_rel"] == helper.SIM_DEVICES_REL
    assert sim["evidence_file"] == helper.SIM_EVIDENCE and sim["side_evidence_file"] == helper.SIDE_EVIDENCE
    assert {"SIM_OUTSIDE_DEVICE", "SIM_NOT_BOUND", "SIM_NOT_OWN_RUN"} <= set(decl["check_codes"])
    assert {"VR_SIM_INVALID", "VR_SIM_EVIDENCE_REJECTED"} <= set(vr.reject_codes())
    vr_src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    assert "def verify_sim_evidence" not in vr_src and "helper.verify_sim_evidence(" in vr_src, "证据核对唯一实现在 helper"
    assert '"xcrun"' not in vr_src, "治理根侧不调 simctl(只在目标主机 helper)"
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
    helper_src = HELPER.read_text(encoding="utf-8")
    simctl_calls = re.findall(r'_simctl\("(\w+)"', helper_src)
    assert sorted(set(simctl_calls)) == ["create", "delete"], simctl_calls
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f194_ios_run_simulator.py", default_test_contract())
