"""AIPOS-F265 件①: 测试夹具子进程(看板 / 门 / serve)共享助手——起进程即新建进程组, teardown / 超时 / 异常路径整组回收, 不留 ppid=1 孤儿。

原状(F263 交回实撞): tools/aipos_cli/tests/test_service_mode.py 起 `serve start`(supervisor 再派生 web.board.app 与门), 用例在
`_wait_for_port` 超时时 finally 只 kill supervisor 本身 → 看板 / 门子进程被过继给 init(ppid=1)长期存活、占端口;
run-all 之外直接跑 pytest 时没有执行器的进程组收尸兜底。

本助手(各测试文件共用, 禁每个文件各写一份):
  spawn(argv, **popen_kwargs)  起夹具进程: 强制新建进程组(会话); 环境追加登记标签 RUNALL_FIXTURE_TAG=<本轮运行号>:<随机串>
                               (本轮运行号 = run-all 执行器下传的 RUNALL_FIXTURE_RUN, 直接跑 pytest = direct-<pid>; 子孙继承)。
  reap(proc)                   收尾: 整组 SIGTERM → 限时等待 → SIGKILL(唯一实现 runall_discovery.terminate_group), 再按登记标签
                               清掉另起会话逃出该组的子孙(terminate_pids), 最后收掉管道。重复调用无害。
  running(argv, **kw)          上下文管理器: try/finally 包住 spawn + reap(断言失败 / 超时异常照样回收)。
  会话退出兜底(atexit): 本进程拉起而未 reap 的夹具一律 reap(测试忘了收尾也不留孤儿; 被 SIGKILL 的测试进程走不到这里,
                               由 run-all 结束时的残留报告按登记标签兜底, runall_discovery.fixture_residual_report)。
只认本助手登记标签精确识别的进程, 不按命令行全机匹配(并行 run-all / 生产门 / 他方进程一概不碰)。
"""
from __future__ import annotations

import atexit
import contextlib
import os
import secrets
import subprocess
from typing import Any, Iterator, Sequence

from tools.aipos_cli.runall_discovery import (
    FIXTURE_GRACE_SECONDS,
    FIXTURE_RUN_ENV,
    FIXTURE_TAG_ENV,
    processes_with_env,
    terminate_group,
    terminate_pids,
)

#: 本进程拉起、尚未 reap 的夹具 {pid: Popen}(atexit 兜底用)。
_LIVE: dict[int, subprocess.Popen] = {}
#: 调用方不得自行指定的 Popen 参数(会让夹具不在自己的新进程组里, 整组回收失效)。
_FORBIDDEN_KWARGS = ("start_new_session", "process_group", "preexec_fn")


def run_id() -> str:
    """本轮运行号: run-all 执行器下传值; 直接跑 pytest = direct-<本进程 pid>。"""
    return os.environ.get(FIXTURE_RUN_ENV) or f"direct-{os.getpid()}"


def spawn(argv: Sequence[str], *, env: dict[str, str] | None = None, **popen_kwargs: Any) -> subprocess.Popen:
    """起一个夹具进程(新进程组 + 登记标签)。返回的 Popen 带属性 fixture_tag(本夹具登记标签值)。"""
    bad = [key for key in _FORBIDDEN_KWARGS if key in popen_kwargs]
    if bad:
        raise ValueError(f"夹具进程组由本助手统一新建, 不接受调用方传 {bad}(AIPOS-F265)")
    tag = f"{run_id()}:{secrets.token_hex(6)}"
    child_env = dict(os.environ if env is None else env)
    child_env[FIXTURE_TAG_ENV] = tag
    proc = subprocess.Popen(list(argv), env=child_env, start_new_session=True, **popen_kwargs)
    proc.fixture_tag = tag  # type: ignore[attr-defined]
    _LIVE[proc.pid] = proc
    return proc


def tagged(proc: subprocess.Popen) -> list[tuple[int, str]]:
    """带本夹具登记标签的存活进程 [(pid, 命令行)](含另起会话逃出进程组的子孙)。"""
    return processes_with_env(f"{FIXTURE_TAG_ENV}={proc.fixture_tag}")  # type: ignore[attr-defined]


def _open_pipes(proc: subprocess.Popen) -> list[Any]:
    return [s for s in (proc.stdin, proc.stdout, proc.stderr) if s is not None and not s.closed]


def reap(proc: subprocess.Popen, *, grace: float = FIXTURE_GRACE_SECONDS, settle: float = 0.0) -> str:
    """收尾: (可选)先给夹具 settle 秒自行退出(同时读管道, 如 `serve stop` 之后等 supervisor 正常退出) → 整组回收(terminate_group:
    SIGTERM → grace 秒 → SIGKILL) → 按登记标签清掉逃出该组的子孙(terminate_pids) → 读完并关掉管道。重复调用无害。
    管道输出存到 proc.fixture_output = (stdout, stderr)(没接管道 / 调用方已读完 = (None, None))。
    返回 terminate_group 的结果: "gone"(收尾时组内已无存活成员) / "terminated"(SIGTERM 内退净) / "killed"(宽限后 SIGKILL)。"""
    output: tuple[Any, Any] = (None, None)
    if settle > 0:
        with contextlib.suppress(subprocess.TimeoutExpired):  # 未自行退出 = 下面整组回收; 已读的输出由下次 communicate 续上
            if _open_pipes(proc):
                output = proc.communicate(timeout=settle)
            else:
                proc.wait(timeout=settle)
    outcome = terminate_group(proc.pid, grace=grace, leader=proc)
    proc.wait()
    escaped = [pid for pid, _cmd in tagged(proc)]
    if escaped:
        terminate_pids(escaped, grace=grace)
    if _open_pipes(proc):
        try:
            output = proc.communicate(timeout=grace)  # 写端(整组 + 逃出子孙)已全部退出, 读到 EOF 即返回
        except subprocess.TimeoutExpired:
            for stream in _open_pipes(proc):  # 仍有未登记的写端占着管道: 不再等, 关掉本端(出声由调用方的断言负责)
                stream.close()
    proc.fixture_output = output  # type: ignore[attr-defined]
    _LIVE.pop(proc.pid, None)
    return outcome


@contextlib.contextmanager
def running(argv: Sequence[str], **kwargs: Any) -> Iterator[subprocess.Popen]:
    """with running([...]) as proc: …——块内断言失败 / 超时异常照样整组回收。"""
    proc = spawn(argv, **kwargs)
    try:
        yield proc
    finally:
        reap(proc)


def _reap_all_live() -> None:
    for proc in list(_LIVE.values()):
        reap(proc)


atexit.register(_reap_all_live)
