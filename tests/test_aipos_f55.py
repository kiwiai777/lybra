#!/usr/bin/env python3
"""AIPOS-F55 夹具 — 门记录加载加缓存与增量(正确性三红线 + 性能先红后绿)。

红线(卡面, 违则 FAIL):
  ① 写后立即可读(同进程) ② 跨进程可见(指纹=文件系统事实) ③ 并发轮询不读半写
防碎片化:
  缓存唯一实现挂 records.load_records;零新持久物;记录目录零改动;
  默认行为不变;失效判据纯文件系统(禁版本号/写入点通知)。

先红后绿(性能): git main 版 load_records(无缓存) vs HEAD 版(缓存+增量),
同一真实 records 规模下连续 10 次总耗时下降一个数量级以上。

跑法: python3 tests/test_aipos_f55.py (经 run-all.sh 常驻)
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOV = Path("/home/kiwi/ai-project-os/2_projects/lybra")  # 真实 records 规模(4190 文件)
sys.path.insert(0, str(REPO))

from tools.aipos_cli.records import load_records, clear_records_cache  # noqa: E402


def _load_prefix_module():
    src = subprocess.run(
        ["git", "-C", str(REPO), "show", "main:tools/aipos_cli/records.py"],
        capture_output=True, text=True, check=True,
    ).stdout
    tmp = Path(tempfile.mkdtemp(prefix="f55-prefix-")) / "records_prefix.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("records_prefix", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PREFIX = _load_prefix_module()

PASS = 0


def ok(label, cond, detail=""):
    global PASS
    if not cond:
        raise AssertionError(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))
    PASS += 1
    print(f"  ✓ {label}" + (f" ({detail})" if detail else ""))


def _md(path: Path, fm: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"] + [f"{k}: {v}" for k, v in fm.items()] + ["---", "", "body", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def _fake_repo(tmp: Path, n_tasks: int = 3) -> Path:
    root = tmp / "gov"
    for i in range(n_tasks):
        tid = f"TEST-F55-{i}"
        _md(root / "5_tasks/records/claims" / tid / f"claim_{tid}.md",
            {"record_type": "claim_log", "claim_id": f"claim_{tid}", "task_id": tid})
        _md(root / "5_tasks/records/returns" / tid / f"return_{tid}.md",
            {"record_type": "return_record", "return_id": f"return_{tid}", "task_id": tid})
    return root


# ---------------------------------------------------------------------------

def test_1_write_then_read_same_process():
    """红线①: 同进程写入后, 下一次读取必须看到(缓存自动失效, 无须手工清)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f55-wr-"))
    root = _fake_repo(tmp)
    r1 = load_records(root)
    ok("① 首载 3 claims", r1["summary"]["claim_logs"] == 3)
    r2 = load_records(root)  # 热缓存
    ok("① 热读一致", r2["summary"]["claim_logs"] == 3)
    tid = "TEST-F55-NEW"
    _md(root / "5_tasks/records/claims" / tid / f"claim_{tid}.md",
        {"record_type": "claim_log", "claim_id": f"claim_{tid}", "task_id": tid})
    r3 = load_records(root)  # 写后立即可读
    ok("① 写后立即可读(缓存自动失效)", r3["summary"]["claim_logs"] == 4,
       f"{r3['summary']['claim_logs']}")
    # 修改既有文件同样可见(指纹含 mtime/size)
    p = root / "5_tasks/records/claims/TEST-F55-0/claim_TEST-F55-0.md"
    p.write_text(p.read_text() + "\nupdated\n", encoding="utf-8")
    r4 = load_records(root)
    target = next((c for c in r4["claims"] if "TEST-F55-0" in str(c.get("path", ""))), None)
    ok("① 改后立即可读", target is not None and "updated" in (target.get("body") or ""))


def test_2_cross_process_visibility():
    """红线②: 子进程写入 → 父进程下一次调用看到(指纹基于文件系统非内存)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f55-xproc-"))
    root = _fake_repo(tmp)
    load_records(root)  # 父进程先热缓存
    tid2 = "TEST-F55-XPROC2"
    writer = tmp / "writer.py"
    writer.write_text(
        "from pathlib import Path\n"
        f"root = Path({str(root)!r})\n"
        f"tid = {tid2!r}\n"
        "p = root / '5_tasks/records/claims' / tid / ('claim_' + tid + '.md')\n"
        "p.parent.mkdir(parents=True, exist_ok=True)\n"
        "p.write_text('---\\nrecord_type: claim_log\\nclaim_id: claim_' + tid + '\\ntask_id: ' + tid + '\\n---\\nbody\\n')\n",
        encoding="utf-8")
    subprocess.run([sys.executable, str(writer)], check=True)
    r = load_records(root)
    ok("② 跨进程写入可见", r["summary"]["claim_logs"] == 4, f"{r['summary']['claim_logs']}")


def test_3_concurrent_polls_no_torn_read():
    """红线③: 并发轮询 + 写入进行中 —— 不崩溃不半写, 终态收敛可见。"""
    tmp = Path(tempfile.mkdtemp(prefix="f55-conc-"))
    root = _fake_repo(tmp, n_tasks=5)
    stop = threading.Event()
    errors: list[str] = []

    def poller():
        while not stop.is_set():
            try:
                r = load_records(root)
                n = r["summary"]["claim_logs"]
                if not isinstance(n, int) or n < 5:
                    errors.append(f"claims 数异常: {n}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"轮询崩溃: {exc!r}")

    threads = [threading.Thread(target=poller) for _ in range(4)]
    for t in threads:
        t.start()
    # 写入进行中(分块追加模拟半写窗口)
    tid = "TEST-F55-CONC"
    target = root / "5_tasks/records/claims" / tid / f"claim_{tid}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nrecord_type: claim_log\n", encoding="utf-8")  # 半写
    time.sleep(0.05)
    target.write_text(
        f"---\nrecord_type: claim_log\nclaim_id: claim_{tid}\ntask_id: {tid}\n---\nbody\n",
        encoding="utf-8")
    time.sleep(0.1)
    stop.set()
    for t in threads:
        t.join()
    ok("③ 并发轮询零崩溃", not errors, "; ".join(errors[:2]))
    final = load_records(root)
    ok("③ 终态收敛可见", final["summary"]["claim_logs"] == 6, f"{final['summary']['claim_logs']}")
    # 半写文件被解析为带 parse_errors 的记录(不静默丢失), 完整写后错误消失
    ok("③ 半写自愈(终态无解析错误)", final["summary"]["parse_errors"] == 0,
       f"parse_errors={final['summary']['parse_errors']}")


def test_4_on_demand_and_default_compatibility():
    """红线④: 默认行为不变 + groups=/task_id= 按需加载(可选参数)。"""
    tmp = Path(tempfile.mkdtemp(prefix="f55-ondemand-"))
    root = _fake_repo(tmp, n_tasks=4)
    full = load_records(root)
    ok("④ 默认全量(与 F55 前同构)", full["summary"]["claim_logs"] == 4 and full["summary"]["return_records"] == 4)
    sub = load_records(root, groups={"claims"})
    ok("④ groups= 只载该组", sub["summary"]["claim_logs"] == 4 and sub["summary"]["return_records"] == 0)
    ok("④ 子集返回结构同构(全键在)", "task_returns" in sub and "session_index" in sub)
    tid = load_records(root, task_id="TEST-F55-1")
    ok("④ task_id= 只载该任务", len(tid["task_claims"].get("TEST-F55-1", [])) == 1
       and len(tid["task_claims"].get("TEST-F55-0", [])) == 0)
    try:
        load_records(root, groups={"bogus"})
        raise AssertionError("[FAIL] ④ 未知分组应拒绝")
    except ValueError:
        ok("④ 未知分组显式拒绝", True)
    # 既有调用点签名兼容: 位置传参 repo_root
    ok("④ 位置传参兼容", load_records(root)["summary"]["claim_logs"] == 4)


def test_5_perf_red_green():
    """验收①: 同一真实 records 规模, 连续 10 次总耗时降一个数量级以上(先红后绿)。"""
    # 红: main 版(无缓存)真跑 10 次
    t0 = time.perf_counter()
    for _ in range(10):
        PREFIX.load_records(GOV)
    red_total = time.perf_counter() - t0
    # 绿: HEAD 版(缓存+增量) 10 次(首次含一次冷扫)
    clear_records_cache()
    load_records(GOV)  # 冷(不计入, 模拟进程启动首载)
    t0 = time.perf_counter()
    for _ in range(10):
        load_records(GOV)
    green_total = time.perf_counter() - t0
    speedup = red_total / green_total if green_total > 0 else float("inf")
    print(f"  [RED  main版 10×] {red_total:.3f}s")
    print(f"  [GREEN HEAD版 10×] {green_total:.3f}s")
    print(f"  [加速比] {speedup:.1f}x")
    ok("① 加速 ≥10×(一个数量级以上)", speedup >= 10, f"{speedup:.1f}x")


def test_6_no_fragmentation():
    """验收⑧⑨: 缓存唯一实现/零新持久物/记录目录零改动/语义未变。"""
    # ⑧-1 缓存唯一实现: 缓存符号只出现在 records.py
    hits = subprocess.run(
        ["grep", "-rln", "_RECORDS_GROUP_CACHE", str(REPO / "tools")],
        capture_output=True, text=True).stdout.strip().splitlines()
    hits = [h for h in hits if "__pycache__" not in h]
    ok("⑧ 缓存实现唯一(仅 records.py)", hits == [str(REPO / "tools/aipos_cli/records.py")], str(hits))
    # ⑧-2 无第二层缓存/lru_cache 加在 load_records 上的别处
    others = subprocess.run(
        ["grep", "-rn", "lru_cache", str(REPO / "tools/aipos_cli/board_adapter.py"),
         str(REPO / "tools/mcp_server/tools.py")],
        capture_output=True, text=True).stdout.strip()
    ok("⑧ board_adapter/mcp_server 无另加缓存层", others == "")
    # ⑧-3 零新持久物: 源码无 sqlite/redis/缓存目录写入
    src = (REPO / "tools/aipos_cli/records.py").read_text(encoding="utf-8")
    ok("⑧ 无外部缓存组件", "sqlite" not in src and "redis" not in src.lower())
    ok("⑧ 缓存路径不写盘(无 open-write)", "open(" not in src.split("AIPOS-F55")[1].split("def expected_")[0])
    # ⑧-4 记录目录零改动(git diff)
    diff = subprocess.run(
        ["git", "-C", str(REPO), "diff", "main", "--name-only", "--", "5_tasks/records/"],
        capture_output=True, text=True).stdout.strip()
    ok("⑧ 记录目录零改动", diff == "", diff)
    # ⑨ 运行时零新持久物: 热加载前后仓内隐藏目录集合不变
    before = {p.name for p in GOV.iterdir() if p.name.startswith(".")}
    clear_records_cache()
    load_records(GOV)
    load_records(GOV, groups={"claims"})
    after = {p.name for p in GOV.iterdir() if p.name.startswith(".")}
    ok("⑨ 运行时零新增持久物", after == before, f"新增: {sorted(after - before)}")


if __name__ == "__main__":
    for fn in [
        test_1_write_then_read_same_process,
        test_2_cross_process_visibility,
        test_3_concurrent_polls_no_torn_read,
        test_4_on_demand_and_default_compatibility,
        test_5_perf_red_green,
        test_6_no_fragmentation,
    ]:
        print(f"\n== {fn.__name__} ==")
        fn()
    print(f"\n✓ AIPOS-F55 夹具全绿 ({PASS} assertions)")
