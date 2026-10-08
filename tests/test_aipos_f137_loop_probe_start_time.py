"""AIPOS-F137 件①②(gap #118): loop status 探活改「进程启动时刻」指纹——改标题的 harness 不再被误判 launch_dead。

病: loop_run_record.process_fingerprint 原以 /proc/<pid>/cmdline sha1 核身; pi(node)启动后改写进程标题(argv 覆写)致命令行变,
活进程被 `lybra loop status` 判 launch_dead(10-08 实测 F135R/F136R2 审计 pi), 顾问据此停 loop 连带终止在跑的审计 pi。
治: 指纹 = 进程启动时刻(Linux /proc/<pid>/stat 第 22 字段 starttime, 按最后一个右括号定位; 无 /proc 用 `ps -o lstart=`)。

靶场: tmp 治理根(test_aipos_f90 rig)+ 本文件自起的假 harness(python, 独立进程组)。运行记录经真实写侧 LoopRunRecorder 落盘、
经真实读侧 loop_status / render_status 判定。清理只按本夹具记录的精确 pid / pgid(禁碰任何非本夹具进程), 收尾断言无残留。

验收(卡面 ★件②): 改标题 → running; 真正被杀(未回收僵尸 / 已回收)→ launch_dead; pid 复用(活着但启动时刻不同)→ 非同一进程;
旧记录(命令行 sha1 指纹)→ 降级「pid 存在即活」并标注「旧指纹, 仅按 pid」; 无 /proc 读法(ps lstart)同判。
"""
from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402  — 靶场唯一来源
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli.loop_driver import load_loop_contract  # noqa: E402

TASK = f90.TASK
MARK = "lybra-f137-fake-harness"
NEW_COMM = "f137) t (x"  # 改后的进程名故意含空格与括号: /proc/<pid>/stat 须按最后一个右括号定位字段

# 假 harness: 起来报 ready; 收到 rename 即改自身进程标题——写 /proc/self/comm + 经 /proc/self/mem 覆写 argv 区(node
# process.title 同一手法); 无 /proc 或覆写不可用时以同 pid re-exec 换 argv(启动时刻不变)。之后阻塞读 stdin 直到被杀。
FAKE = r'''
import os, sys
def say(x):
    sys.stdout.write(x + "\n"); sys.stdout.flush()
def rename():
    if os.path.isdir("/proc/self"):
        try:
            raw = open("/proc/self/stat", "rb").read()
            rest = raw[raw.rindex(b")") + 2:].split()
            a0, a1 = int(rest[45]), int(rest[46])  # stat 第 48/49 字段 arg_start / arg_end
            title = b"renamed-title"
            with open("/proc/self/mem", "r+b", buffering=0) as mem:
                mem.seek(a0)
                mem.write(title.ljust(a1 - a0, b"\0"))
            with open("/proc/self/comm", "w") as comm:
                comm.write(sys.argv[3])
            return "mem"
        except (OSError, ValueError, IndexError):
            pass
    os.execv(sys.executable, [sys.executable, "-c", os.environ["F137_FAKE_CODE"], "renamed-by-exec", "exec", sys.argv[3]])
if sys.argv[2] == "exec":
    say("renamed exec")
else:
    say("ready")
    if sys.stdin.readline().strip() == "rename":
        say("renamed " + rename())
sys.stdin.read()
'''


def _show(line: str) -> None:
    print(line, flush=True)


def _cmdline(pid: int) -> bytes:
    if os.path.isdir("/proc/self"):
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    return subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, check=True).stdout.strip()


def _legacy_fp(cmdline: bytes) -> str:
    """F131 原读法(命令行 sha1 前 16 位)——只用于证明改标题后旧判据必误判, 产品代码已不用。"""
    return hashlib.sha1(cmdline).hexdigest()[:16]


@pytest.fixture
def fakes():
    """本夹具起的假 harness(独立进程组)。收尾只按记录的精确 pgid 清理, 并断言无残留。"""
    procs: list[subprocess.Popen] = []

    def spawn() -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", FAKE, MARK, "start", NEW_COMM], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, text=True, start_new_session=True,
                                env={**os.environ, "F137_FAKE_CODE": FAKE})
        procs.append(proc)
        assert proc.stdout.readline().strip() == "ready"
        return proc

    yield spawn
    for proc in procs:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)  # 本夹具自起进程组(start_new_session: pgid == pid)
        proc.wait(timeout=10)
        for stream in (proc.stdin, proc.stdout):
            stream.close()
    leftover = [p.pid for p in procs if lrr.process_fingerprint(p.pid) is not None]
    assert leftover == [], f"残留假 harness 进程: {leftover}"


def _rename(proc: subprocess.Popen) -> str:
    proc.stdin.write("rename\n")
    proc.stdin.flush()
    line = proc.stdout.readline().strip()
    assert line.startswith("renamed "), line
    return line.split()[1]


def _recorder(gov: Path) -> lrr.LoopRunRecorder:
    warnings: list[str] = []
    rec = lrr.LoopRunRecorder(gov, TASK, driver=DRIVER, contract=load_loop_contract(), warn=warnings.append)
    assert warnings == []
    return rec


def _launch(rec: lrr.LoopRunRecorder, proc: subprocess.Popen) -> None:
    """经真实写侧落一次拉起(launch_started 现读 process_fingerprint)。"""
    plan = SimpleNamespace(card=TASK, harness="pi", location={"transport": "local", "host": None}, cwd="/fixture/workstation")
    harness = SimpleNamespace(pid=proc.pid, pgid=proc.pid, remote_pgid=None, counts={})
    rec.launch_started(plan, harness)


def _status(gov: Path) -> tuple[dict, str]:
    report = lrr.loop_status(gov, TASK)
    assert len(report["runs"]) == 1, report
    text = lrr.render_status(report, lrr.run_record_declaration(load_loop_contract())["states"])
    return report["runs"][0], text


def _rewrite_launch(rec: lrr.LoopRunRecorder, **fields) -> None:
    rec.meta["launches"][-1].update(fields)
    rec.flush(force=True)


def test_fingerprint_is_start_time_and_survives_title_change(rig, fakes):  # noqa: F811
    """件②-1 改标题: 命令行与进程名都变(旧 cmdline 指纹必变), 启动时刻指纹不变 → status 判 running(非 launch_dead)。"""
    proc = fakes()
    rec = _recorder(rig.gov)
    _launch(rec, proc)
    recorded = rec.meta["launches"][-1]["process_fingerprint"]
    assert recorded.startswith(lrr.FINGERPRINT_PREFIX_PROC if os.path.isdir("/proc/self") else lrr.FINGERPRINT_PREFIX_PS)
    assert not lrr.fingerprint_is_legacy(recorded) and lrr.fingerprint_is_legacy(rec.meta["process_fingerprint"]) is False
    before = _cmdline(proc.pid)
    how = _rename(proc)
    after = _cmdline(proc.pid)
    _show(f"[件②-1 改标题] 方式={how} 命令行 {before[:60]!r} → {after[:60]!r}")
    assert MARK.encode() in before and MARK.encode() not in after
    assert _legacy_fp(before) != _legacy_fp(after)  # F131 旧判据(命令行指纹)在此必判 launch_dead
    if how == "mem":
        assert Path(f"/proc/{proc.pid}/comm").read_text().strip() == NEW_COMM  # 进程名含 ") (": 字段定位须抗
    assert lrr.process_fingerprint(proc.pid) == recorded
    view, text = _status(rig.gov)
    _show(f"[件②-1 改标题后 status]\n{text}")
    assert view["state"] == "running" and view["launch"]["alive"] is True, view
    assert "probe_note" not in view["launch"] and "loop_probe_note" not in view
    assert lrr.LEGACY_FINGERPRINT_NOTE not in text
    rec.end(outcome="completed", exit_code=0, reason="completed", message="fixture")


def test_killed_harness_is_launch_dead_zombie_and_reaped(rig, fakes):  # noqa: F811
    """件②-2 真正被杀: 未回收(僵尸)与已回收两种情形都判 launch_dead。"""
    proc = fakes()
    rec = _recorder(rig.gov)
    _launch(rec, proc)
    assert _status(rig.gov)[0]["state"] == "running"
    os.killpg(proc.pid, signal.SIGKILL)  # 本夹具自起进程组
    os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)  # 同步等到已退出但不回收: 此刻必为僵尸
    view, text = _status(rig.gov)
    _show(f"[件②-2 被杀(未回收僵尸)status]\n{text}")
    assert view["state"] == "launch_dead" and view["launch"]["alive"] is False, view
    proc.wait(timeout=10)
    assert lrr.process_fingerprint(proc.pid) is None
    view, text = _status(rig.gov)
    _show(f"[件②-2 被杀(已回收)status]\n{text}")
    assert view["state"] == "launch_dead" and view["launch"]["alive"] is False, view
    rec.end(outcome="completed", exit_code=0, reason="completed", message="fixture")


def test_zombie_counts_as_dead(fakes):
    """僵尸(已死未回收)= 不在: /proc stat 第 3 字段 Z。等到内核把它标为僵尸再断言(同步: wait 前用 waitid WNOWAIT 阻塞等退出)。"""
    proc = fakes()
    fp = lrr.process_fingerprint(proc.pid)
    os.killpg(proc.pid, signal.SIGKILL)
    os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)  # 阻塞至已退出, 不回收 → 此刻必为僵尸
    assert lrr.process_fingerprint(proc.pid) is None
    assert lrr.process_alive(proc.pid, fp) is False
    proc.wait(timeout=10)


def test_pid_reuse_different_start_time_is_not_same_process(rig, fakes):  # noqa: F811
    """件②-3 pid 复用: 记录的指纹属进程 A, 该 pid 上现在是启动时刻不同的进程 B(B 活着)→ 判非同一进程(launch_dead)。"""
    a = fakes()
    fp_a = lrr.process_fingerprint(a.pid)
    for _ in range(5):  # B 在 A 报 ready(解释器已起)之后才 fork; starttime 精度 1 个时钟滴答, 同滴答则再起一个(有界, 非轮询)
        b = fakes()
        fp_b = lrr.process_fingerprint(b.pid)
        if fp_b != fp_a:
            break
    _show(f"[件②-3] A pid={a.pid} {fp_a}; B pid={b.pid} {fp_b}")
    assert fp_a and fp_b and fp_a != fp_b
    rec = _recorder(rig.gov)
    _launch(rec, b)
    _rewrite_launch(rec, process_fingerprint=fp_a)  # = 「pid B 曾属 A」: 记录时是 A, 现读是 B
    view, text = _status(rig.gov)
    _show(f"[件②-3 pid 复用 status]\n{text}")
    assert view["state"] == "launch_dead" and view["launch"]["alive"] is False, view
    assert lrr.process_alive(b.pid, fp_a) is False and lrr.process_alive(b.pid, fp_b) is True
    # loop 自身同判: 记录的 loop 指纹不属现进程 = loop_dead
    rec.meta["process_fingerprint"] = fp_a
    rec.flush(force=True)
    assert _status(rig.gov)[0]["state"] == "loop_dead"
    rec.end(outcome="completed", exit_code=0, reason="completed", message="fixture")


def test_legacy_cmdline_fingerprint_degrades_to_pid_only_with_note(rig, fakes):  # noqa: F811
    """旧记录兼容: 命令行 sha1 指纹(16 位十六进制)→ pid 存在即活, status 标注「旧指纹, 仅按 pid」; pid 不在 → launch_dead。"""
    proc = fakes()
    rec = _recorder(rig.gov)
    _launch(rec, proc)
    stale = _legacy_fp(b"some-old-cmdline\0")  # 与现命令行无关的旧格式值(= 改过标题后的旧记录)
    assert lrr.fingerprint_is_legacy(stale)
    _rewrite_launch(rec, process_fingerprint=stale)
    rec.meta["process_fingerprint"] = _legacy_fp(b"old-loop-cmdline\0")
    rec.flush(force=True)
    view, text = _status(rig.gov)
    _show(f"[旧记录兼容 status]\n{text}")
    assert view["state"] == "running" and view["launch"]["alive"] is True and view["loop_alive"] is True, view
    assert view["launch"]["probe_note"] == view["loop_probe_note"] == lrr.LEGACY_FINGERPRINT_NOTE == "旧指纹, 仅按 pid"
    assert text.count("旧指纹, 仅按 pid") == 2
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    view, text = _status(rig.gov)
    assert view["state"] == "launch_dead" and "旧指纹, 仅按 pid" in text, view
    rec.end(outcome="completed", exit_code=0, reason="completed", message="fixture")


def test_ps_reading_without_proc_same_verdicts(fakes, monkeypatch):
    """无 /proc 读法(macOS 路径, 本机强制走 ps -o stat=,lstart=, LC_ALL=C): 改标题指纹不变; 启动时刻不符 = 非同一进程; 被杀 = None。
    (lstart 精度 1 秒: 同一秒内 pid 被复用的极端情形不可分辨, 如实披露。)"""
    monkeypatch.setattr(lrr, "_proc_available", lambda: False)
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")  # 调用方 locale 不影响: 读法内固定 C
    a = fakes()
    fp = lrr.process_fingerprint(a.pid)
    _show(f"[ps 读法] pid={a.pid} {fp}")
    assert fp.startswith(lrr.FINGERPRINT_PREFIX_PS) and " " not in fp and not lrr.fingerprint_is_legacy(fp)
    _rename(a)
    assert lrr.process_fingerprint(a.pid) == fp and lrr.process_alive(a.pid, fp) is True
    assert lrr.process_alive(a.pid, lrr.FINGERPRINT_PREFIX_PS + "Thu_Jan_1_00:00:00_1970") is False  # 启动时刻不符 = 非同一进程
    os.killpg(a.pid, signal.SIGKILL)
    os.waitid(os.P_PID, a.pid, os.WEXITED | os.WNOWAIT)
    assert lrr.process_fingerprint(a.pid) is None  # 僵尸(stat Z)= 不在
    a.wait(timeout=10)
    assert lrr.process_fingerprint(a.pid) is None and lrr.process_alive(a.pid, fp) is False


def test_malformed_stat_fails_closed(monkeypatch):
    """/proc/<pid>/stat 形不合 = LoopRunRecordError(fail-closed, 不静默当活/当死)。"""
    real_read = Path.read_bytes

    def fake_read(self):
        if str(self) == f"/proc/{os.getpid()}/stat":
            return b"123 (name) S 1 2"
        return real_read(self)

    monkeypatch.setattr(lrr, "_proc_available", lambda: True)
    monkeypatch.setattr(Path, "read_bytes", fake_read)
    with pytest.raises(lrr.LoopRunRecordError, match="第 22 字段"):
        lrr.process_fingerprint(os.getpid())
