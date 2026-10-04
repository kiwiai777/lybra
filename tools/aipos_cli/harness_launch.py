"""AIPOS-F95 件③ — lybra loop 拉起 harness 的进程封装(由 loop_driver._launched_wait 唯一调用)。

一次拉起 = 一个新进程组(start_new_session)、无 shell、stdin=/dev/null、cwd=工位目录; stdout 事件流逐行汇总为一行式进度
(enums.schema harness.launch.events 格式; 未知类型只计数), stderr 只留末尾若干行(verbs.schema lybra_loop.launch)。
pump(seconds) 作为唯一哨兵 run_fs_watch 的 sleeper: select 事件驱动阻塞读输出至多 seconds 秒, 非 sleep 自旋。
terminate_group: SIGTERM → 等声明秒数 → SIGKILL 整组(含孙进程)。无守护/调度/心跳/常驻: 进程只活在一次等待内。
token/凭据永不上屏: 凭据字样所在行整行隐去, 长不透明串打码。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
from typing import Any, Callable


class LoopInterrupted(Exception):
    """拉起等待期间 loop 收到 SIGINT/SIGTERM: 进程组已终止, 调用方按该信号退出(不新增退出码)。"""

    def __init__(self, signum: int) -> None:
        super().__init__(f"signal {signum}")
        self.signum = signum


# 一行式进度: 凭据字样所在行整行隐去; 长不透明串(≥32 位且同含大写/小写/数字, 如 urlsafe token)打码
_CREDENTIAL_WORD_RE = re.compile(r"(?i)secret|token|password|passwd|private[_-]?key|credential|api[_-]?key|bearer|authorization|LYBRAENROLL")
_OPAQUE_RE = re.compile(r"[A-Za-z0-9_\-+=]{32,}")
_PI_QUIET_EVENTS = frozenset({"session", "agent_start", "turn_start", "turn_end", "message_start", "message_update",
                              "tool_execution_update", "queue_update", "compaction_start", "compaction_end"})


def redact_progress(text: str) -> str:
    if _CREDENTIAL_WORD_RE.search(text):
        return "[含凭据字样, 整行已隐去]"

    def mask(m: re.Match[str]) -> str:
        s = m.group(0)
        opaque = any(c.isupper() for c in s) and any(c.islower() for c in s) and any(c.isdigit() for c in s)
        return "[已打码]" if opaque else s

    return _OPAQUE_RE.sub(mask, text)


def _first_line(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False) if value is not None else ""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def summarize_event(line: str, fmt: str) -> tuple[str | None, str]:
    """一行事件 → (一行式摘要 | None, 计数类别)。fmt = enums.schema harness.launch.events; 未知格式只计数。"""
    if fmt != "pi-json":
        return None, "raw"
    try:
        event = json.loads(line)
    except ValueError:
        return None, "non_json"
    if not isinstance(event, dict):
        return None, "non_json"
    kind = str(event.get("type") or "")
    if kind == "tool_execution_start":
        args = event.get("args")
        first = next(iter(args.values()), "") if isinstance(args, dict) and args else args
        return f"工具 {event.get('toolName')}: {_first_line(first)}", "summarized"
    if kind == "tool_execution_end":
        if event.get("isError"):
            result = event.get("result")
            if isinstance(result, dict) and isinstance(result.get("content"), list):
                result = " ".join(str(c.get("text") or "") for c in result["content"] if isinstance(c, dict))
            return f"工具 {event.get('toolName')} 出错: {_first_line(result)}", "summarized"
        return None, "quiet"
    if kind == "message_end":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message.get("role") != "assistant":
            return None, "quiet"
        if message.get("stopReason") == "error" or message.get("errorMessage"):
            return f"错误: {_first_line(message.get('errorMessage') or 'stopReason=error')}", "summarized"
        content = message.get("content")
        texts = [str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text"] if isinstance(content, list) else []
        first = _first_line("\n".join(texts))
        return (f"助手: {first}", "summarized") if first else (None, "quiet")
    if kind == "agent_end":
        return "agent 结束", "summarized"
    if kind in _PI_QUIET_EVENTS:
        return None, "quiet"
    return None, f"unknown:{kind or '?'}"


class LaunchedHarness:
    """一次拉起的 harness 进程(新进程组、无 shell、stdin=/dev/null)。pump = 注入 watch 的 sleeper(select 阻塞读输出, 非自旋)。"""

    _MAX_PARTIAL = 1 << 20

    def __init__(self, plan: Any, decl: dict[str, Any], say: Callable[[str], None]) -> None:
        import collections
        import selectors
        import subprocess

        self.plan, self.say = plan, say
        self.max_chars = int(decl["progress_max_chars"])
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=int(decl["stderr_tail_lines"]))
        self.counts: collections.Counter[str] = collections.Counter()
        self.proc = subprocess.Popen(plan.argv, cwd=plan.cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        self.pid = self.pgid = self.proc.pid  # start_new_session → setsid: 进程组号 = pid
        self.prefix = f"{plan.harness} {plan.card} pid={self.pid}"
        self._sel = selectors.DefaultSelector()
        self._bufs: dict[str, bytes] = {}
        for name, stream in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr)):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                self._sel.register(stream, selectors.EVENT_READ, name)
                self._bufs[name] = b""

    # -- 输出 --
    def _line(self, name: str, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip("\r")
        if name == "stderr":
            if text.strip():
                self.stderr_tail.append(redact_progress(text)[: self.max_chars])
            return
        summary, kind = summarize_event(text, self.plan.events)
        self.counts[kind] += 1
        if summary:
            self.say(f"  [{self.prefix}] {redact_progress(summary)[: self.max_chars]}")

    def _feed(self, name: str, chunk: bytes) -> None:
        *lines, rest = (self._bufs[name] + chunk).split(b"\n")
        self._bufs[name] = rest[-self._MAX_PARTIAL:]
        for raw in lines:
            self._line(name, raw)

    def _close_stream(self, key: Any) -> None:
        name = key.data
        self._sel.unregister(key.fileobj)
        key.fileobj.close()
        if self._bufs.get(name):
            self._line(name, self._bufs[name])
        self._bufs[name] = b""

    def pump(self, seconds: float) -> None:
        """阻塞读输出至多 seconds 秒(select 事件驱动); 管道都关闭且进程已退出则提前返回。"""
        import subprocess

        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            remaining = deadline - time.monotonic()
            if not self._sel.get_map():
                if remaining > 0 and self.proc.poll() is None:
                    try:
                        self.proc.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        pass  # 宽限/间隔到点, 进程仍在 = 交调用方按声明处置(超时/终止), 非吞错
                return
            if remaining <= 0:
                return
            for key, _mask in self._sel.select(timeout=remaining):
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if chunk:
                    self._feed(key.data, chunk)
                else:
                    self._close_stream(key)

    def exited(self) -> bool:
        return self.proc.poll() is not None

    # -- 生命周期 --
    def terminate_group(self, wait_seconds: float) -> str:
        """终止整个进程组: SIGTERM → 等 wait_seconds → SIGKILL; 已退出则只清残留。返回处置说明。"""
        import signal as _signal
        import subprocess

        how = "already_exited" if self.proc.poll() is not None else "sigterm"
        try:
            os.killpg(self.pgid, _signal.SIGTERM)
        except ProcessLookupError:
            pass  # 组内已无进程 = 已清干净
        try:
            self.proc.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            how = "sigkill"
        try:
            os.killpg(self.pgid, _signal.SIGKILL)  # 清组内残留(含孙进程)
        except ProcessLookupError:
            pass  # 组内已无进程
        if self.proc.poll() is None:
            self.proc.wait()
        return how

    def close(self) -> None:
        for key in list(self._sel.get_map().values()):
            self._close_stream(key)
        self._sel.close()

    def counts_line(self) -> str:
        unknown = {k.split(":", 1)[1]: v for k, v in self.counts.items() if k.startswith("unknown:")}
        return (f"  [{self.prefix}] 事件计数: 汇总 {self.counts['summarized']}, 静默 {self.counts['quiet']}, "
                f"非 JSON {self.counts['non_json']}, 原样 {self.counts['raw']}, 未知类型 {unknown or '无'}")


@contextlib.contextmanager
def signals_raise_interrupt() -> Any:
    """拉起等待期间: SIGINT/SIGTERM → LoopInterrupted(调用方 finally 先清进程组)。非主线程不装(signal 只能主线程装)。"""
    import signal as _signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _handler(signum: int, _frame: Any) -> None:
        raise LoopInterrupted(signum)

    previous = {sig: _signal.signal(sig, _handler) for sig in (_signal.SIGTERM, _signal.SIGINT)}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            _signal.signal(sig, handler)


@contextlib.contextmanager
def signals_deferred() -> Any:
    """清进程组期间: SIGINT/SIGTERM 只记下(不打断清理), 退出上下文后恢复原处置; 调用方据记录再抛 LoopInterrupted。"""
    import signal as _signal
    import threading

    received: list[int] = []
    if threading.current_thread() is not threading.main_thread():
        yield received
        return

    def _record(signum: int, _frame: Any) -> None:
        received.append(signum)

    previous = {sig: _signal.signal(sig, _record) for sig in (_signal.SIGTERM, _signal.SIGINT)}
    try:
        yield received
    finally:
        for sig, handler in previous.items():
            _signal.signal(sig, handler)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
