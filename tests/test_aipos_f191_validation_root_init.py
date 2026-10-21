"""AIPOS-F191 — 验证根首建目录: run 目录链(<base>/<卡ID>/<run-id>)由产品 helper init-run 在目标主机正式初始化。

靶场 = F190 chris 形靶场(同一 rig: 临时治理根 + Codex 子 agent 执行者在他机 + 本机审计工位; 模拟远端主机 sim-mac = 临时目录);
目标主机侧 = 对 helper 原文经标准输入起子进程(`python3 - init-run …`, 与开工稿给出的管道形同一运行方式, helper 不落盘)。
禁真门 / 真治理根 / 真工位 / 真 pi / 真 Mac。

验收原文经 _show 打到 stdout(RETURN 证据): ①base 不存在 → 建出三级目录(0700)+ 幂等; ②软链 / 某级为文件 / 越界 / run 已结束 /
撤销 / 指纹不符 / 到期 / 祖先不可写拒; ③审计实例建执行卡层拒; ④run-start 输出与开工稿 / 章程新步骤原文(同源)。
"""
from __future__ import annotations

import ast
import json
import os
import shlex
import stat
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
FETCH_TAIL = " | python3 - init-run "  # AIPOS-F203: 前半段 = 本 run 固定 helper 取用(`… helper --id <声明> --run-id <run>`)


def _init_args(command: str) -> list[str]:
    """产品铸的命令 = `<治理根所在机取本 run 固定 helper> | python3 - init-run <参数>`; 取参数部分。"""
    assert FETCH_TAIL in command and " project validation-root helper --id " in command.split(FETCH_TAIL, 1)[0], command
    return shlex.split(command.split(FETCH_TAIL, 1)[1])


def _host_run(args: list[str]) -> tuple[int, str]:
    """模拟目标主机: helper 原文经管道交给 `python3 -`(与开工稿同一运行方式)。"""
    proc = subprocess.run([sys.executable, "-", "init-run", *args], input=HELPER.read_text(encoding="utf-8"), capture_output=True,
                          text=True, timeout=60)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _init_command(gov: Path, decl_id: str, *extra: str) -> tuple[int, str, str]:
    return _vr(gov, "init-command", "--id", decl_id, *extra)


def _mode(path: Path) -> str:
    return oct(stat.S_IMODE(os.lstat(path).st_mode))


def _entry(gov: Path, decl_id: str) -> dict:
    from tools.aipos_cli.validation_root import project_validation_roots

    return next(e for e in project_validation_roots(gov) if e["id"] == decl_id)


# ===========================================================================
# 件① base 不存在 → init-run 建出三级目录; 幂等
# ===========================================================================

def test_item1_init_run_creates_chain_then_idempotent(rig):
    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    rc, out, err = _run_start(rig, EXEC_DECL)
    assert rc == 0, out + err
    line = next(ln for ln in out.splitlines() if "下一步(run-start 后第一步)" in ln)
    command = line.split(" 运行: ", 1)[1].split("(只建空目录链", 1)[0]
    run_dir = base / CARD / "run-1"
    rig["mac"].mkdir(parents=True)  # 模拟 Mac: 项目 scratch 已在(用户可写), base 及以下不存在(chris HBJOTA-60 实撞形)
    assert not base.exists()
    rc, host_out = _host_run(_init_args(command))
    _show(f"---- 件① 目标主机 init-run(base 不存在)exit {rc} ----\n{host_out}")
    assert rc == 0 and host_out.splitlines()[-1].startswith(f"INIT_OK run_dir={run_dir} created=3 ")
    for level in (base, base / CARD, run_dir):
        assert level.is_dir() and not level.is_symlink() and _mode(level) == "0o700", level
        assert f"mkdir  {level} 0o700" in host_out
    listing = "\n".join(f"{_mode(p)} {p}" for p in (base, base / CARD, run_dir))
    _show(f"[件① 三级目录与权限]\n{listing}")
    assert sorted(p.name for p in run_dir.iterdir()) == [".lybra-vr"], "run 目录内只有证据目录(不写其他文件)"
    assert sorted(p.name for p in (base / CARD).iterdir()) == ["run-1"] and sorted(p.name for p in base.iterdir()) == [CARD], \
        "base / 卡层只建空目录链, 不写文件"
    evidence = [json.loads(ln) for ln in (run_dir / ".lybra-vr" / "init-run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert evidence[0]["created"] == [str(base), str(base / CARD), str(run_dir)] and evidence[0]["fingerprint"] == _fp()

    rc, again = _host_run(_init_args(command))
    _show(f"---- 件① 再跑(幂等)exit {rc} ----\n{again}")
    assert rc == 0 and again.splitlines()[-1] == f"INIT_OK run_dir={run_dir} created=0 evidence={run_dir}/.lybra-vr/init-run.jsonl"
    assert f"exists {run_dir} 0o700" in again and "mkdir" not in again
    assert len((run_dir / ".lybra-vr" / "init-run.jsonl").read_text(encoding="utf-8").splitlines()) == 2, "每次初始化追加一条证据"

    # base / 卡层仍不许角色写文件(建目录只经 init-run)
    rc, out, _ = _wb(gov, "--instance", EXEC, "--task-id", CARD, "--host", HOST, "--run-id", "run-1", "--level", "mutate",
                     "--check", f"{base}/notes.txt")
    _show(f"[件① base 层写文件] exit {rc}: {out.strip()}")
    assert rc == 3 and "DENY [OUTSIDE_TASK_RUN]" in out and "helper init-run" in out and "角色不得自行 mkdir" in out


# ===========================================================================
# 件② 软链 / 某级为文件 / 越界 / run 已结束 / 撤销 / 指纹不符 / 到期 / 祖先不可写拒
# ===========================================================================

def test_item2_refusals(rig):
    from tools.aipos_cli import validation_root as vr
    from tools.aipos_cli.validation_root_helper import host_fingerprint, plan_init_chain

    gov, base, tmp = rig["gov"], rig["base"], rig["tmp"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0
    entry = _entry(gov, EXEC_DECL)
    run = vr.active_run(entry)

    def minted(**over) -> list[str]:  # 产品唯一铸造口对(该主机上)不同 base / 指纹 / 到期的声明铸出的命令
        return _init_args(vr.init_run_command(gov, {**entry, **over}, run))

    real = tmp / "real-mac"
    real.mkdir()
    (tmp / "mac-link").symlink_to(real)
    (tmp / "filelevel").write_text("x", encoding="utf-8")
    locked = tmp / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    good = minted()
    run_dir = f"{base}/{CARD}/run-1"
    tampered = list(good)
    tampered[tampered.index("--run-dir") + 1] = f"{tmp}/elsewhere/{CARD}/run-1"
    cases = [
        ("base 祖先含软链", minted(base=f"{tmp}/mac-link/scratch/{CARD}"), "DENY [CHAIN_SYMLINK]", f"{tmp}/mac-link 是符号链接"),
        ("某级为文件", minted(base=f"{tmp}/filelevel/{CARD}"), "DENY [CHAIN_NOT_DIRECTORY]", f"{tmp}/filelevel 已存在但不是目录"),
        ("越界(改 run 目录到 base 外)", tampered, "DENY [INIT_SEAL_MISMATCH]", "命令须原样取自产品"),
        ("指纹不符(他机)", minted(host_fingerprint=host_fingerprint("another-machine-id")), "DENY [HOST_FINGERPRINT_MISMATCH]", "跨机调用"),
        ("声明到期", minted(expires_at="2026-01-01T00:00:00Z"), "DENY [DECLARATION_EXPIRED]", "2026-01-01T00:00:00Z 到期"),
        ("最近已存在祖先不可写(须提权)", minted(base=f"{locked}/vr/{CARD}"), "DENY [ANCESTOR_NOT_WRITABLE]", "禁 sudo"),
    ]
    try:
        for label, args, needle, reason in cases:
            rc, out = _host_run(args)
            _show(f"[件② helper {label}] exit {rc}: {out}")
            assert rc == 3 and needle in out and reason in out, (label, out)
    finally:
        locked.chmod(0o700)
    assert not Path(f"{tmp}/elsewhere").exists() and not (real / "scratch").exists() and not (locked / "vr").exists(), "拒 = 零 mkdir"
    assert not Path(run_dir).exists()
    bad = plan_init_chain(str(base), f"{base}/{CARD}/run-1/deeper")
    _show(f"[件② run 目录层级不合] {bad['code']}: {bad['reason']}")
    assert bad["code"] == "INIT_CHAIN_INVALID"

    # run 已结束 / 声明撤销: 治理根侧不铸命令(拒原文); 开工稿不再列初始化步
    rc, out, err = _init_command(gov, EXEC_DECL)
    assert rc == 0 and "init-run" in out, err
    rc, out, err = _vr(gov, "run-finish", "--id", EXEC_DECL, "--run-id", "run-1", "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    rc, out, err = _init_command(gov, EXEC_DECL)
    _show(f"[件② run 已结束] init-command exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_STATE_CONFLICT" in err and "无活动 run" in err and not out
    rc, brief, _ = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    assert rc == 0 and "目录链初始化(AIPOS-F191)" not in brief and "当前无活动 run" in brief
    assert _run_start(rig, EXEC_DECL)[0] == 0
    rc, out, err = _vr(gov, "revoke", "--id", EXEC_DECL, "--reason", "撤销测试", "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    rc, out, err = _init_command(gov, EXEC_DECL)
    _show(f"[件② 声明已撤销] init-command exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_STATE_CONFLICT" in err and "revoked" in err
    rc, out, err = _init_command(gov, "vr-nope")
    assert rc == 1 and "VR_NOT_FOUND" in err


# ===========================================================================
# 件③ 审计实例只能建审计卡目录链(共用 base 时互不能建对方卡层)
# ===========================================================================

def test_item3_auditor_cannot_init_executor_card_layer(rig):
    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    assert _run_start(rig, EXEC_DECL)[0] == 0
    assert _run_start(rig, AUD_DECL)[0] == 0
    rc, out, err = _init_command(gov, EXEC_DECL, "--instance", AUD)
    _show(f"[件③ 审计实例取执行声明的初始化命令] exit {rc}: {err.strip()}")
    assert rc == 1 and "VR_TASK_NOT_OWNED" in err and f"属于实例 {EXEC}, 不是 {AUD}" in err

    rc, out, err = _init_command(gov, AUD_DECL, "--instance", AUD)
    _show(f"---- 件③ 审计实例自己的 init-command exit {rc} ----\n{out}")
    assert rc == 0, err
    own = _init_args(out.splitlines()[-1])
    forged = list(own)
    forged[forged.index("--run-dir") + 1] = f"{base}/{CARD}/run-1"
    rc, host_out = _host_run(forged)
    _show(f"[件③ 审计命令改卡段建执行卡层] exit {rc}: {host_out}")
    assert rc == 3 and "DENY [INIT_SEAL_MISMATCH]" in host_out and not (base / CARD).exists()

    rc, host_out = _host_run(own)
    _show(f"---- 件③ 审计实例 init-run(只建审计卡链; base 之上缺失的祖先同样只建空目录)exit {rc} ----\n{host_out}")
    assert rc == 0 and (base / AUDIT_CARD / "run-1").is_dir() and not (base / CARD).exists()
    assert _mode(rig["mac"]) == "0o700" and f"mkdir  {rig['mac']}" in host_out
    for inst, task, layer in ((AUD, AUDIT_CARD, CARD), (EXEC, CARD, AUDIT_CARD)):
        rc, out, _ = _wb(gov, "--instance", inst, "--task-id", task, "--host", HOST, "--run-id", "run-1", "--level", "mutate",
                         "--check", f"{base}/{layer}")
        _show(f"[件③ write-boundary {inst} 建对方卡层 {layer}] exit {rc}: {out.strip()}")
        assert rc == 3 and "DENY [NOT_OWN_DECLARATION]" in out and "互不能建" in out


# ===========================================================================
# 件② run-start 输出 / 开工稿 / 章程新步骤(同源)
# ===========================================================================

def test_item4_run_start_output_kickoff_and_charter(rig):
    gov, base = rig["gov"], rig["base"]
    _declare_both(rig)
    rc, out, err = _vr(gov, "run-start", "--id", EXEC_DECL, "--candidate", rig["candidate"], "--actor", ADVISOR)
    _show(f"---- 件② run-start --dry-run exit {rc} ----\n{out}{err}")
    assert rc == 0 and "确认后下一步(run-start 后第一步)" in out and "init-run" in out
    preview = out.split(" 运行: ", 1)[1].split("(只建空目录链", 1)[0]
    rc, out, err = _run_start(rig, EXEC_DECL)
    _show(f"---- 件② run-start --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and f"下一步(run-start 后第一步): 在目标主机 {HOST} 运行: lybra --workspace-root" in out
    rc, cmd_out, err = _init_command(gov, EXEC_DECL, "--instance", EXEC)
    _show(f"---- 件② init-command exit {rc} ----\n{cmd_out}")
    command = cmd_out.splitlines()[-1]
    assert rc == 0 and command in out and command == preview, "run-start 预演 / 确认输出与 init-command 同一铸造口"
    rc, js, _ = _init_command(gov, EXEC_DECL, "--json")
    assert rc == 0 and json.loads(js)["command"] == command and json.loads(js)["run_dir"] == f"{base}/{CARD}/run-1"

    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    _show(f"---- 件② kickoff(chris 形, run-1 活动)验证根节 ----\n{brief[brief.index('## 跨机临时验证根'):brief.index('## 禁写与禁止动作')]}")
    assert rc == 0, err
    lines = brief.splitlines()
    first = next(i for i, ln in enumerate(lines) if "当前活动 run: `run-1`" in ln)
    assert "① run-start 后第一步 = 目录链初始化(AIPOS-F191)" in lines[first + 1] and f"`{command}`" in lines[first + 1], \
        "开工稿把 init-run 列为 run-start 后第一步(紧随活动 run 行)"
    assert "只认末行 `INIT_OK` + 退出码 0" in brief and "禁自行 mkdir / sudo / 改参数重试" in brief
    assert "目录链(base / 卡层 / run 目录)由产品 helper init-run 在目标主机初始化(只建空目录), 角色不得自行 mkdir base / 卡层" in brief
    assert f"{base}/{AUDIT_CARD}" not in brief

    rc, charter, err = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", gov)
    section = charter[charter.index("## 🔴 写权限边界"):]
    # AIPOS-F206: 章程只留通用规则(目录链只经 helper init-run), 本 run 的 init-run 命令只在开工稿(上文已核)
    assert rc == 0 and "目录链只经产品 helper init-run 在目标主机建空目录, 角色不得自行 mkdir base / 卡层" in section
    assert f"`{command}`" not in section and "run-1" not in section, "章程不列本 run 命令明细"


def test_single_source_seal_and_helper_stdlib():
    from tools.aipos_cli import validation_root as vr

    vr_src = (REPO_ROOT / "tools" / "aipos_cli" / "validation_root.py").read_text(encoding="utf-8")
    assert "def init_seal" not in vr_src and "helper.init_seal(" in vr_src, "封印唯一实现在 helper(目标主机同一份)"
    assert vr_src.count("def init_run_command") == 1 and "init_run_command(" in vr_src.split("def apply_validation_root_change", 1)[1]
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported
    assert "init_run" in vr.TEXT_KEYS and vr.section_texts()["init_run"]
