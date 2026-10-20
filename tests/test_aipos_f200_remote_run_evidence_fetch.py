"""AIPOS-F200 — 远端验证 run 的审计证据: 审计 run 在目标主机(Mac)时, 产品命令把 run 证据按清单与指纹取回审计报告落点副本,
门对副本做与本机 run 同一核对(gap #205, chris 理想 iOS UI 将撞)。

件① 主机侧清单: helper evidence-manifest(封签防改参)列本 run 证据目录(roles.schema validation_root.run_evidence.include_dirs)下文件
    (相对路径 / 大小 / sha256)+ 主机指纹, 写 <run>/.lybra-vr/evidence-manifest.json; 软链 / 非普通文件 / 超限 = 拒; 代码文件不取回(skipped)。
件② 取回: `lybra project validation-root evidence-fetch --id <审计声明> --run-id <run>` 经声明 ssh 别名在目标主机只读运行 helper evidence-bundle,
    只收清单与清单内规范路径的普通文件, 落 <verdict_root>/<审计卡>/run_evidence/<run_id>/, 以裁决入口同一函数 evidence_copy_check 复核才替换。
件③ 门核对: runtime_evidence_check 对远端 run 改读副本(副本核对 → src-verify 读副本 → 日志读副本), 判据与本机 run 同一函数; 无副本 = 拒并给命令。
件④ 渲染: 审计开工稿 / 派审注入「验证卡运行证据步骤」与章程验证根节在 run 位于远端时追加清单 + 取回两步(同源)。

靶场 = AIPOS-F196/F199 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 「目标主机」sim-mac / kiwiai-mac = 本机模拟, 假 ssh 把远端命令
就地交 sh 执行并记录别名, base 在 tmp)。禁真门 / 真治理根 / 真工位 / 真 chris / 真 Mac / 真 pi。验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源(禁第二份)
from test_aipos_f78_engine_agnostic import AUDITOR, DRIVER, EXEC  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f196_validation_to_pi import ALIAS, HOST, OWNER_REF, VAL, _fp, _show, _vr, vrig  # noqa: E402,F401  — F196 靶场
from test_aipos_f199_validation_audit_run_evidence import (  # noqa: E402  — F199 靶场(验证卡 + 审计证据判据入口)
    CMDS, VAL2, VAL2R, VALR, _check, _report, _run, _run_start, _setup_cards,
)
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli import validation_root as vr  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _declare_remote(gov: Path, base: Path, task_id: str, instance: str, *, host: str = HOST) -> str:
    rc, out, err = _vr(gov, "declare", "--host", host, "--base", base, "--task-id", task_id, "--instance", instance, "--owner-ref", OWNER_REF,
                       "--host-fingerprint", _fp(), "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err
    return next(e["id"] for e in vr.project_validation_roots(gov) if e["task_id"] == task_id and e["instance"] == instance and e["host"] == host)


def _set_host(gov: Path, host: str = HOST, alias: str = ALIAS) -> None:
    rc, out, err = _vr(gov, "set-host", "--host", host, "--ssh-alias", alias, "--owner-ref", OWNER_REF, "--actor", DRIVER, "--confirm")
    assert rc == 0, out + err


def _entry(gov: Path, decl: str) -> dict:
    return next(e for e in vr.project_validation_roots(gov) if e["id"] == decl)


def _prepare_remote_run(gov: Path, decl: str) -> Path:
    """目标主机(经假 ssh): 产品铸的 init-run → 候选源码送达 → src-verify(末行 SRC_OK)。返回 run 目录。"""
    info = vr.init_command(gov, decl)
    for label, command in (("init-run", info["command"]), ("送达", info["source_delivery"]["deliver"]),
                           ("src-verify", info["source_delivery"]["verify"])):
        proc = _run(command)
        assert proc.returncode == 0, f"{label}: {proc.stdout}{proc.stderr}"
    return Path(info["run_dir"])


def _remote_exec(run_dir: Path, candidate: str, tree: str, alias: str = ALIAS) -> list[dict]:
    """在目标主机本 run 内(经 ssh)按卡面声明命令运行, 日志写本 run logs/(随证据清单取回); 记录的 command = 渲染后的声明命令(逐字)。"""
    from tools.aipos_cli.artifact_ingest import render_candidate_evidence_command

    values = {"run_dir": str(run_dir), "src_dir": str(run_dir / "src"), "candidate": candidate, "candidate_tree": tree}
    entries = []
    for i, declared in enumerate(CMDS, 1):
        command = render_candidate_evidence_command(declared, values)
        log = run_dir / "logs" / f"cmd{i}.log"
        remote = f"mkdir -p {shlex.quote(str(log.parent))} && cd {shlex.quote(str(run_dir / 'src'))} && {{ {command} ; }} > {shlex.quote(str(log))} 2>&1"
        proc = subprocess.run(["ssh", alias, remote], capture_output=True, text=True, timeout=60)
        entries.append({"command": command, "run_id": run_dir.name, "run_dir": str(run_dir), "exit_code": proc.returncode,
                        "log_path": str(log), "log_sha256": _sha(log)})
    return entries


def _manifest(gov: Path, decl: str) -> subprocess.CompletedProcess:
    entry = _entry(gov, decl)
    return _run(vr.evidence_manifest_command(gov, entry, vr.active_run(entry)))


def _fetch(gov: Path, decl: str, run_id: str) -> tuple[int, str, str]:
    return _vr(gov, "evidence-fetch", "--id", decl, "--run-id", run_id)


@pytest.fixture
def remote(vrig):  # noqa: F811
    """远端审计 run 已就绪: 验证卡 + 审计实例在 sim-mac 的声明 + 别名 + run-start + 目录链 / 送达 / SRC_OK + 卡面命令已在本 run 运行。"""
    gov, base = vrig.gov, vrig.base
    candidate, tree = _setup_cards(vrig)
    _set_host(gov)
    decl = _declare_remote(gov, base, VALR, AUDITOR)
    _run_start(gov, decl, candidate)
    run_dir = _prepare_remote_run(gov, decl)
    entries = _remote_exec(run_dir, candidate, tree)
    vrig.candidate, vrig.tree, vrig.decl, vrig.run_dir, vrig.entries = candidate, tree, decl, run_dir, entries
    vrig.copy = vr.run_evidence_copy_dir(gov, _entry(gov, decl), run_dir.name)
    return vrig


# ===========================================================================
# 验收①: 远端审计 run → 清单 → 取回 → 门核对通过
# ===========================================================================

def test_acceptance1_remote_run_manifest_fetch_gate_pass(remote):
    gov, decl, run_dir, entries = remote.gov, remote.decl, remote.run_dir, remote.entries
    _show(f"---- 验收① 远端审计 run(声明 {decl}, 主机 {HOST}, 别名 {ALIAS})实跑记录 ----\n{json.dumps(entries, ensure_ascii=False, indent=1)}")
    assert all(e["exit_code"] == 0 for e in entries)

    # 无副本 = 拒并给清单与取回命令
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收① 取回前·裁决入口拒(无副本)]\n  " + bad["reasons"][0])
    assert f"在主机 {HOST}(非治理根所在机), 裁决入口读不到 run 内送达核对证据与日志(fail-closed)" in bad["reasons"][0]
    assert "审计报告落点无取回副本" in bad["reasons"][0] and "evidence-manifest" in bad["reasons"][0]
    assert f"project validation-root evidence-fetch --id {decl} --run-id {run_dir.name}" in bad["reasons"][0]

    # 主机侧: 产品铸的清单命令(经 ssh, helper 管道)
    (run_dir / "logs" / "helper.sh").write_text("echo not fetched\n", encoding="utf-8")  # 代码文件: 不取回, 记 skipped
    proc = _manifest(gov, decl)
    _show(f"[验收① 件① evidence-manifest 经 ssh] exit {proc.returncode}\n{proc.stdout}{proc.stderr}")
    assert proc.returncode == 0 and proc.stdout.strip().splitlines()[-1].startswith("EVIDENCE_OK manifest=")
    assert "skipped logs/helper.sh" in proc.stdout
    manifest = json.loads((run_dir / ".lybra-vr" / "evidence-manifest.json").read_text(encoding="utf-8"))
    _show(f"[验收① 件① 主机侧清单] files={[f['path'] for f in manifest['files']]} fingerprint={manifest['fingerprint']} seal={manifest['seal']}")
    assert manifest["fingerprint"] == _fp() == _entry(gov, decl)["host_fingerprint"]
    assert {f["path"] for f in manifest["files"]} >= {"logs/cmd1.log", "logs/cmd2.log", ".lybra-vr/src-verify.jsonl", ".lybra-vr/init-run.jsonl"}
    assert not any(f["path"].startswith("src/") for f in manifest["files"])

    # 治理根所在机: evidence-fetch(经声明别名只读拉取)
    remote.ssh_log.write_text("", encoding="utf-8")
    rc, out, err = _fetch(gov, decl, run_dir.name)
    _show(f"---- 验收① 件② evidence-fetch exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"FETCH_OK copy_dir={remote.copy}" in out
    ssh_lines = remote.ssh_log.read_text(encoding="utf-8").strip().splitlines()
    _show("[验收① 件② 假 ssh 记录]\n  " + "\n  ".join(ssh_lines))
    assert ssh_lines == [f"{ALIAS}\tpython3 - evidence-bundle --run-dir {run_dir} --expect-fingerprint {_fp()}"]
    assert sorted(p.relative_to(remote.copy).as_posix() for p in remote.copy.rglob("*") if p.is_file()) == sorted(
        ["manifest.json", "fetch.json", *(f"files/{f['path']}" for f in manifest["files"])])
    assert not (remote.copy / "files" / "logs" / "helper.sh").exists()

    ok = _check(gov, VALR, VAL, remote.candidate, entries)
    _show(f"[验收① 件③ 裁决入口对副本核对] {ok}")
    assert ok is None

    # 同一份记录经 artifact ingest 入门前预检(同一判据)= OK
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "harness": "pi", "audit": "", "audit_by": ""})
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    _report(gov, VALR, VAL, remote.candidate, entries)
    got = validate_task_artifact(VALR, gov)
    _show(f"[验收① artifact ingest 预检] ok={got['ok']} category={got['category']} reasons={got.get('reasons')}")
    assert got["ok"] and got["category"] == "OK"

    # run 收口后照样可取回 / 核对(取回只读, 不写已收口 run)
    assert _vr(gov, "run-finish", "--id", decl, "--run-id", run_dir.name, "--actor", DRIVER, "--confirm")[0] == 0
    rc, out, err = _fetch(gov, decl, run_dir.name)
    assert rc == 0, out + err
    assert _check(gov, VALR, VAL, remote.candidate, entries) is None
    _show("[验收① 收口后重新取回 + 核对] 通过")


# ===========================================================================
# 验收②: 篡改远端日志 / 篡改副本 / 清单外文件 / 软链 / 超限 / 封签不符 / 取回缺失 / 执行 run 冒充 各拒
# ===========================================================================

def test_acceptance2_tamper_and_escape_refusals(remote, monkeypatch):
    gov, decl, run_dir, entries, copy = remote.gov, remote.decl, remote.run_dir, remote.entries, remote.copy
    assert _manifest(gov, decl).returncode == 0
    assert _fetch(gov, decl, run_dir.name)[0] == 0
    assert _check(gov, VALR, VAL, remote.candidate, entries) is None

    def refetch() -> None:
        assert _manifest(gov, decl).returncode == 0
        rc, out, err = _fetch(gov, decl, run_dir.name)
        assert rc == 0, out + err

    # ① 篡改远端日志(清单生成后): 取回拒, 副本不动
    log1 = run_dir / "logs" / "cmd1.log"
    original = log1.read_bytes()
    before = _sha(copy / "files" / "logs" / "cmd1.log")
    log1.write_bytes(original + b"tampered\n")
    rc, out, err = _fetch(gov, decl, run_dir.name)
    _show(f"[验收② 拒·篡改远端日志] exit {rc}\n{err}")
    assert rc == 1 and "VR_EVIDENCE_REJECTED" in err and "EVIDENCE_FILE_CHANGED" in err and "evidence-manifest" in err
    assert _sha(copy / "files" / "logs" / "cmd1.log") == before
    # 篡改后重生成清单再取回: 清单指纹变了, 报告自述日志 sha256 对不上 = 裁决入口拒
    refetch()
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·篡改远端日志后重生成清单(日志指纹不符)]\n  " + bad["reasons"][0])
    assert "cmd1.log sha256=" in bad["reasons"][0] and "≠ 报告自述" in bad["reasons"][0]
    log1.write_bytes(original)
    refetch()

    # ② 篡改副本
    (copy / "files" / "logs" / "cmd2.log").write_text("改过\n", encoding="utf-8")
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·篡改副本]\n  " + bad["reasons"][0])
    assert "副本文件 logs/cmd2.log sha256=" in bad["reasons"][0] and "副本被改" in bad["reasons"][0]
    refetch()

    # ③ 清单外文件(副本内多塞)
    (copy / "files" / "logs" / "extra.log").write_text("smuggled\n", encoding="utf-8")
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·清单外文件]\n  " + bad["reasons"][0])
    assert "副本内 files/logs/extra.log 是清单外文件" in bad["reasons"][0]
    refetch()

    # ④ 软链: 主机侧证据目录内软链 = 清单拒; 清单后换成软链 = 打包拒; 副本内软链 = 裁决入口拒
    secret = remote.gov.parent / "outside-secret.txt"
    secret.write_text("outside\n", encoding="utf-8")
    (run_dir / "logs" / "link.log").symlink_to(secret)
    proc = _manifest(gov, decl)
    _show(f"[验收② 拒·主机侧证据目录软链] exit {proc.returncode} {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [EVIDENCE_SYMLINK]" in proc.stdout
    (run_dir / "logs" / "link.log").unlink()
    assert _manifest(gov, decl).returncode == 0
    log2 = run_dir / "logs" / "cmd2.log"
    saved = log2.read_bytes()
    log2.unlink()
    log2.symlink_to(secret)
    rc, out, err = _fetch(gov, decl, run_dir.name)
    _show(f"[验收② 拒·清单后换成软链(打包)] exit {rc}\n{err}")
    assert rc == 1 and "EVIDENCE_SYMLINK" in err
    log2.unlink()
    log2.write_bytes(saved)
    refetch()
    target = copy / "files" / "logs" / "cmd2.log"
    target.unlink()
    target.symlink_to(secret)
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·副本内软链]\n  " + bad["reasons"][0])
    assert "副本内 files/logs/cmd2.log 是符号链接" in bad["reasons"][0]
    refetch()

    # ⑤ 超限(上限读声明; 靶场把单文件上限压到 4096 字节, 命令封签随声明重铸)
    real = vr.run_evidence_declaration()
    monkeypatch.setattr(vr, "run_evidence_declaration", lambda: {**real, "max_file_bytes": 4096})
    (run_dir / "logs" / "huge.log").write_bytes(b"x" * 5000)
    proc = _manifest(gov, decl)
    _show(f"[验收② 拒·超限] exit {proc.returncode} {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [EVIDENCE_TOO_LARGE]" in proc.stdout and "超单文件上限 4096" in proc.stdout
    (run_dir / "logs" / "huge.log").unlink()
    bad = _check(gov, VALR, VAL, remote.candidate, entries)  # 旧副本按旧上限生成 ≠ 现行声明 = 拒(须重生成并重取)
    _show("[验收② 拒·副本上限 ≠ 现行声明]\n  " + bad["reasons"][0])
    assert "副本清单 limits=" in bad["reasons"][0]
    monkeypatch.setattr(vr, "run_evidence_declaration", lambda: real)
    refetch()

    # ⑥ 封签不符: 改清单命令参数 / 改主机侧清单 / 改副本清单
    entry = _entry(gov, decl)
    command = vr.evidence_manifest_command(gov, entry, vr.active_run(entry))
    forged = command.replace("--max-files 1000", "--max-files 999999")
    assert forged != command
    proc = _run(forged)
    _show(f"[验收② 拒·清单命令改参] exit {proc.returncode} {proc.stdout.strip()}")
    assert proc.returncode == 3 and "DENY [EVIDENCE_SEAL_MISMATCH]" in proc.stdout
    host_manifest = run_dir / ".lybra-vr" / "evidence-manifest.json"
    data = json.loads(host_manifest.read_text(encoding="utf-8"))
    host_manifest.write_text(json.dumps({**data, "candidate": "f" * 40}, ensure_ascii=False), encoding="utf-8")
    rc, out, err = _fetch(gov, decl, run_dir.name)
    _show(f"[验收② 拒·主机侧清单被改(封签不符)] exit {rc}\n{err}")
    assert rc == 1 and "EVIDENCE_MANIFEST_INVALID" in err and "封签不符" in err
    refetch()
    copy_manifest = copy / "manifest.json"
    cdata = json.loads(copy_manifest.read_text(encoding="utf-8"))
    copy_manifest.write_text(json.dumps({**cdata, "fingerprint": "hfp1:" + "0" * 32}, ensure_ascii=False), encoding="utf-8")
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·副本清单指纹被改]\n  " + bad["reasons"][0])
    assert "副本证据清单 fingerprint=" in bad["reasons"][0]
    refetch()

    # ⑦ 取回缺失: 副本缺清单内文件 / 整个副本不在
    (copy / "files" / ".lybra-vr" / "src-verify.jsonl").unlink()
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·副本缺清单内文件]\n  " + bad["reasons"][0])
    assert "副本缺清单内文件 .lybra-vr/src-verify.jsonl(取回缺失)" in bad["reasons"][0]
    shutil.rmtree(copy)
    bad = _check(gov, VALR, VAL, remote.candidate, entries)
    _show("[验收② 拒·无副本]\n  " + bad["reasons"][0])
    assert "审计报告落点无取回副本" in bad["reasons"][0]
    refetch()

    # ⑧ 日志不在远端 run 内 / 不在清单内
    outside = [{**entries[0], "log_path": str(remote.gov / "task_cards" / VALR / "x.log")}, entries[1]]
    bad = _check(gov, VALR, VAL, remote.candidate, outside)
    _show("[验收② 拒·日志不在远端 run 内]\n  " + bad["reasons"][0])
    assert "不在远端 run 目录" in bad["reasons"][0]
    late = run_dir / "logs" / "late.log"
    late.write_text("written after manifest\n", encoding="utf-8")
    unlisted = [{**entries[0], "log_path": str(late), "log_sha256": _sha(late)}, entries[1]]
    bad = _check(gov, VALR, VAL, remote.candidate, unlisted)
    _show("[验收② 拒·日志不在取回清单]\n  " + bad["reasons"][0])
    assert "不在取回清单内" in bad["reasons"][0]
    assert _check(gov, VALR, VAL, remote.candidate, entries) is None
    _show("[验收② 复原后 = 通过]")


def test_acceptance2_executor_run_cannot_pose_as_audit_run(remote):
    gov, base, decl, copy = remote.gov, remote.base, remote.decl, remote.copy
    exec_decl = _declare_remote(gov, base, VAL, EXEC)
    _run_start(gov, exec_decl, remote.candidate)
    exec_run = _prepare_remote_run(gov, exec_decl)
    exec_entries = _remote_exec(exec_run, remote.candidate, remote.tree)
    proc = _manifest(gov, exec_decl)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # 执行 run 的记录 = 拒(执行 / 审计隔离)
    bad = _check(gov, VALR, VAL, remote.candidate, exec_entries)
    _show("[验收② 拒·执行 run 冒充审计 run(记录引用执行 run)]\n  " + bad["reasons"][0])
    assert f"引用被审执行卡 {VAL} 的验证 run 目录" in bad["reasons"][0]
    # 对执行声明取回 = 拒
    rc, out, err = _fetch(gov, exec_decl, exec_run.name)
    _show(f"[验收② 拒·对执行声明 evidence-fetch] exit {rc}\n{err}")
    assert rc == 1 and "VR_EVIDENCE_REJECTED" in err and "只供审计实例自己的 run" in err
    # 把执行 run 的清单与证据塞进审计副本 = 拒(run 目录不符)
    assert _manifest(gov, decl).returncode == 0 and _fetch(gov, decl, remote.run_dir.name)[0] == 0
    shutil.copyfile(exec_run / ".lybra-vr" / "evidence-manifest.json", copy / "manifest.json")
    bad = _check(gov, VALR, VAL, remote.candidate, remote.entries)
    _show("[验收② 拒·执行 run 清单塞进审计副本]\n  " + bad["reasons"][0])
    assert "副本证据清单 run_dir=" in bad["reasons"][0]
    # 他卡审计 run 的副本不算本卡
    other = _declare_remote(gov, base, VAL2R, AUDITOR)
    _run_start(gov, other, remote.candidate)
    other_run = _prepare_remote_run(gov, other)
    other_entries = _remote_exec(other_run, remote.candidate, remote.tree)
    bad = _check(gov, VALR, VAL, remote.candidate, other_entries)
    _show("[验收② 拒·他卡 run]\n  " + bad["reasons"][0])
    assert f"是卡 {VAL2R} 的 run, 不是本审计卡 {VALR} 的" in bad["reasons"][0]
    # 本机 run 不经取回
    local_decl = _declare_remote(gov, base / "local", VAL2R, AUDITOR, host=vr.local_host_label())
    rc, out, err = _fetch(gov, local_decl, "run-1")
    _show(f"[验收② 本机 run evidence-fetch] exit {rc}\n{err}")
    assert rc == 1 and "是本机(治理根所在机)" in err
    # 未声明 ssh 别名的主机
    nohost = _declare_remote(gov, base / "nohost", VALR, AUDITOR, host="other-mac")
    _run_start(gov, nohost, remote.candidate)
    rc, out, err = _fetch(gov, nohost, "run-1")
    _show(f"[验收② 拒·主机未声明 ssh 别名] exit {rc}\n{err}")
    assert rc == 1 and "主机 other-mac 未声明 ssh 别名" in err and "set-host --host other-mac" in err


# ===========================================================================
# 验收③④: 渲染(审计开工稿 / 派审注入 / 章程验证根节同源)+ chris 形态只读预览
# ===========================================================================

def test_item4_remote_steps_rendered_same_source(remote):
    from tools.aipos_cli.audit_derivation import build_forensic_anchor_section
    from tools.aipos_cli.subagent_kickoff import render_kickoff_brief

    gov, decl, run_dir = remote.gov, remote.decl, remote.run_dir
    entry = _entry(gov, decl)
    manifest_cmd = vr.evidence_manifest_command(gov, entry, vr.active_run(entry))
    fetch_cmd = vr.evidence_fetch_command(gov, entry, run_dir.name)
    assert manifest_cmd.startswith(f"lybra --workspace-root {gov} project validation-root helper | ssh {ALIAS} 'python3 - evidence-manifest ")
    f78._card(gov, VALR, "claimed", assigned=AUDITOR, task_mode="audit",
              extra={"reviewed_task_id": VAL, "claimed_by": AUDITOR, "agent_instance": AUDITOR, "harness": "pi", "audit": "", "audit_by": "",
                     "claim_id": f"claim_{VALR}_x", "claimed_at": "2026-10-21T00:00:00Z"})
    f78._claim_record(gov, VALR, AUDITOR)
    val_path, _q = nr._find_task_in_queue(gov, VAL)
    card = build_forensic_anchor_section(VAL, gov, nr._read_frontmatter(val_path), audit_task_id=VALR)
    text = render_kickoff_brief(gov, VALR)["text"]
    steps = text[text.index("### 验证卡运行证据步骤"):]
    steps = steps[:steps.index("## 禁写与禁止动作")]
    _show(f"---- 件④ 审计开工稿「验证卡运行证据步骤」(run 在远端 {HOST})----\n{steps}")
    for needle in ("⑧ run 在目标主机", manifest_cmd, "⑨ 在治理根所在机取回证据", fetch_cmd, str(remote.copy)):
        assert needle in steps, needle
        assert needle in card, needle  # 派审注入同一渲染
    section = "\n".join(vr.render_section_lines(gov, instance=AUDITOR, task_id=VALR))
    _show("---- 件④ 章程 / 开工稿验证根节(审计实例)证据取回行 ----\n" + next(ln for ln in section.splitlines() if "AIPOS-F200" in ln))
    assert "③ 远端 run 证据取回(AIPOS-F200" in section and manifest_cmd in section and fetch_cmd in section
    # 审计实例在目标主机本机(Codex 子 agent 形, 无 ssh 形): 清单命令为本机管道形, 取回仍由治理根所在机经声明别名拉取(对等)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vr, "ssh_alias_for", lambda *_a, **_k: None)
        local_form = vr.evidence_manifest_command(gov, entry, vr.active_run(entry))
    _show(f"[件④ 审计在目标主机本机形] 清单命令: {local_form[:160]}… 取回: {fetch_cmd}")
    assert local_form.startswith(f"lybra --workspace-root {gov} project validation-root helper | python3 - evidence-manifest --run-dir {run_dir} ")
    # 本机 run: 无远端两步(行为不变)
    local = _declare_remote(gov, remote.base / "local", VAL2R, AUDITOR, host=vr.local_host_label())
    _run_start(gov, local, remote.candidate)
    from tools.aipos_cli.artifact_ingest import render_candidate_evidence_steps

    src_fm = nr._read_frontmatter(nr._find_task_in_queue(gov, VAL2)[0])
    lines = render_candidate_evidence_steps(gov, audit_task_id=VAL2R, audit_instance=AUDITOR, reviewed_task_id=VAL2, reviewed_fm=src_fm,
                                            requirement={"required": True, "commands": list(CMDS)})
    assert not any(ln.startswith(("⑧", "⑨")) for ln in lines)
    assert "AIPOS-F200" not in "\n".join(vr.render_section_lines(gov, instance=AUDITOR, task_id=VAL2R))


def test_acceptance4_chris_shape_preview(vrig):  # noqa: F811
    """chris 形态(审计 pi 在治理根所在机 dev, 经 ssh 别名 kiwiai-mac 操作目标主机 kiwiai-mac 上的审计 run): 只读预览审计步骤渲染原文。
    靶场自造人肉期样本(tmp 治理根; 禁写 chris, 禁对 kiwiai-mac 操作——假 ssh 不出本机)。"""
    gov, base = vrig.gov, vrig.base
    candidate, _tree = _setup_cards(vrig)
    _set_host(gov, host="kiwiai-mac", alias="kiwiai-mac")
    decl = _declare_remote(gov, base, VALR, AUDITOR, host="kiwiai-mac")
    _run_start(gov, decl, candidate)
    from tools.aipos_cli.artifact_ingest import render_candidate_evidence_steps

    src_fm = nr._read_frontmatter(nr._find_task_in_queue(gov, VAL)[0])
    lines = render_candidate_evidence_steps(gov, audit_task_id=VALR, audit_instance=AUDITOR, reviewed_task_id=VAL, reviewed_fm=src_fm,
                                            requirement={"required": True, "commands": list(CMDS)})
    _show("---- 验收④ chris 形态只读预览: 理想 Mac UI 验证卡审计步骤(审计 pi 在 dev, 经 ssh kiwiai-mac)----\n" + "\n".join(lines))
    text = "\n".join(lines)
    assert "| ssh kiwiai-mac 'python3 - evidence-manifest " in text and f"evidence-fetch --id {decl} --run-id run-1" in text
    assert "| ssh kiwiai-mac 'python3 - init-run " in text and "| ssh kiwiai-mac 'tar -x -C " in text


# ===========================================================================
# 单源 / 声明
# ===========================================================================

def test_single_source_and_declarations():
    import ast

    ba = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    funcs = {n.name: ast.unparse(n) for n in ast.parse(ba).body if isinstance(n, ast.FunctionDef)}
    body = funcs["_candidate_run_problem"]
    for name in ("remote_run_evidence(", "evidence_log_file(", "src_verify_evidence_check(", "files_root="):
        assert name in body, name
    src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    vfuncs = {n.name: ast.unparse(n) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    assert "evidence_copy_check(" in vfuncs["evidence_fetch"] and "evidence_copy_check(" in vfuncs["remote_run_evidence"]
    assert src.count("def evidence_copy_check") == 1 and src.count("helper.evidence_seal(") == 1
    helper_src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root_helper.py").read_text(encoding="utf-8")
    assert helper_src.count("def evidence_seal") == 1 and "evidence_manifest_problems(" in vfuncs["evidence_copy_check"]
    roles = json.loads((REPO_ROOT / "schema" / "roles.schema.json").read_text(encoding="utf-8"))
    decl = roles["write_boundary"]["validation_root"]["run_evidence"]
    cfg = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    codes = cfg["configuration_sources"]["project_json"]["schema"]["validation_roots"]["reject_codes"]
    trans = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
    cand = trans["artifact_ingest"]["verdict"]["runtime_evidence"]["candidate_subject"]
    _show(f"[单源·声明] run_evidence.include_dirs={decl['include_dirs']} 上限 file={decl['max_file_bytes']} total={decl['max_total_bytes']} "
          f"files={decl['max_files']}; reject VR_EVIDENCE_REJECTED; remote_steps={len(cand['remote_steps'])}")
    assert "VR_EVIDENCE_REJECTED" in codes and len(cand["remote_steps"]) == 2
    assert vr.helper.EVIDENCE_MANIFEST.startswith(".lybra-vr/") and ".lybra-vr" in decl["include_dirs"]
    assert set(decl["copy_layout"]) == {"manifest", "fetch_record", "files"}
    assert os.sep == "/"
