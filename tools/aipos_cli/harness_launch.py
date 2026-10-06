"""AIPOS-F95 件③ — lybra loop 拉起 harness 的进程封装(由 loop_driver._launched_wait 唯一调用)。

一次拉起 = 一个新进程组(start_new_session)、无 shell、stdin=/dev/null、cwd=工位目录; stdout 事件流逐行汇总为一行式进度
(enums.schema harness.launch.events 格式; 未知类型只计数), stderr 只留末尾若干行(verbs.schema lybra_loop.launch)。
pump(seconds) 作为唯一哨兵 run_fs_watch 的 sleeper: select 事件驱动阻塞读输出至多 seconds 秒, 非 sleep 自旋。
terminate_group: SIGTERM → 等声明秒数 → SIGKILL 整组(含孙进程)。无守护/调度/心跳/常驻: 进程只活在一次等待内。
token/凭据永不上屏: 凭据字样所在行整行隐去, 长不透明串打码。
AIPOS-F107 件①: loop 的中断信号 = SIGINT/SIGTERM/SIGHUP(loop_signals() 唯一声明; ssh 断线 = SIGHUP, 与 SIGTERM 同语义: 先清进程组再按该信号退出);
拉起等待与清理期 SIGPIPE 忽略。输出端断开(ssh 断线后终端 EIO / 管道读端关闭 BrokenPipeError)→ TolerantOutput 转为静默丢弃, 不打断清理。
AIPOS-F110: transport=remote(跨机工位)同一分层——本地进程 = ssh(声明 verbs.schema lybra_loop.launch.remote), 远端固定脚本在工位目录
以新进程组起同一 harness 模板, kickoff 经 ssh stdin 原字节传入, 组号首行回传; 终止 = 经 ssh 对远端组 TERM→KILL 再清本地 ssh 组。
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


# ---------------------------------------------------------------------------
# AIPOS-F110 件①: transport=remote(ssh)。声明 verbs.schema lybra_loop.launch.remote; 与 local 共用 LaunchedHarness / pump /
# 事件汇总 / terminate_group(按 plan.remote 分支), 不另起第二拉起实现。远端脚本固定(不拼值), 值一律作位置参数逐个引号化;
# kickoff 只走 ssh stdin(shell 危险字符 ` $( ${ 换行 均不进任何命令行; 原 kickoff 危险字符常量模块已随 AIPOS-F103 删除)。
# ---------------------------------------------------------------------------

_REMOTE_KEYS = ("ssh_argv", "remote_shell", "pgid_marker", "pgid_timeout_seconds", "probe_timeout_seconds", "kill_timeout_seconds")

# 拉起: $1=工位目录 $2=组号行前缀 $3..=harness.launch.argv 模板(整参数 {kickoff} 占位); stdin = kickoff 原字节(读尽, 保留尾随换行)。
# set -m: 后台作业 = 新进程组, 作业首进程打印自身 pid(= 组号)后 exec harness(stdin=/dev/null); 外层 wait 透传退出码。
_REMOTE_LAUNCH_SCRIPT = (
    'cd -- "$1" || exit 3\n'
    'm=$2\n'
    'shift 2\n'
    'k=$(cat; printf x) || exit 5\n'
    'k=${k%x}\n'
    'for a do\n'
    '  shift\n'
    '  if [ "$a" = "{kickoff}" ]; then set -- "$@" "$k"; else set -- "$@" "$a"; fi\n'
    'done\n'
    'set -m\n'
    'sh -c \'printf "%s%s\\n" "$1" "$$"; shift; exec "$@"\' lybra-harness "$m" "$@" </dev/null &\n'
    'wait "$!"\n'
)
# 探测: $1=工位目录 $2=harness 可执行; 3 = 目录不存在, 4 = 可执行不在远端非交互 PATH
_REMOTE_PROBE_SCRIPT = '[ -d "$1" ] || exit 3\ncommand -v "$2" >/dev/null 2>&1 || exit 4\nexit 0\n'
# 对远端进程组发信号: $1=信号名 $2=组号; 0 = 已发, 1 = 组已不存在, 2 = 组在但发不出
_REMOTE_SIGNAL_SCRIPT = 'kill -s "$1" -- "-$2" 2>/dev/null && exit 0\nkill -s 0 -- "-$2" 2>/dev/null && exit 2\nexit 1\n'
_SSH_UNREACHABLE = 255


class RemoteLaunchError(OSError):
    """跨机拉起失败(ssh 不可达 / 远端脚本失败 / 组号未回传); 本地 ssh 进程组已清。调用方按「拉起失败」处置(exit 3 + 手工提示)。"""


def remote_declaration(decl: dict[str, Any]) -> dict[str, Any]:
    """verbs.schema lybra_loop.launch.remote。缺键/形变 = ValueError(fail-closed)。"""
    remote = decl.get("remote") if isinstance(decl, dict) else None
    if not isinstance(remote, dict) or any(k not in remote for k in _REMOTE_KEYS):
        raise ValueError(f"verbs.schema.json lybra_loop.launch.remote 未声明齐 {list(_REMOTE_KEYS)}")
    argv = remote["ssh_argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError(f"lybra_loop.launch.remote.ssh_argv 须为非空串数组: {argv!r}")
    if not str(remote["remote_shell"]).strip() or not str(remote["pgid_marker"]).strip():
        raise ValueError("lybra_loop.launch.remote.remote_shell / pgid_marker 须为非空串")
    return remote


def _check_ssh_host(host: str) -> None:
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise ValueError(f"ssh 目标非法(空 / 以 - 开头 / 含空白): {host!r}")


def remote_command(remote: dict[str, Any], host: str, script: str, name: str, args: list[str]) -> list[str]:
    """ssh argv = 声明前缀 + [host, `<remote_shell> -c <固定脚本> <name> <参数...>`](每段 shlex 引号化; 远端登录 shell 只做去引号)。"""
    import shlex

    _check_ssh_host(host)
    return [*[str(a) for a in remote["ssh_argv"]], host,
            shlex.join([str(remote["remote_shell"]), "-c", script, name, *[str(a) for a in args]])]


def remote_launch_command(remote: dict[str, Any], host: str, workstation: str, argv_template: list[str]) -> list[str]:
    """拉起用 ssh argv: 参数 = 工位目录 + 组号行前缀 + harness.launch.argv 模板原样(含整参数 {kickoff}; kickoff 本身只走 stdin)。"""
    return remote_command(remote, host, _REMOTE_LAUNCH_SCRIPT, "lybra-launch",
                          [workstation, str(remote["pgid_marker"]), *argv_template])


def _ssh_failure_text(proc: Any) -> str:
    err = (proc.stderr or "").strip().splitlines()
    return redact_progress(err[-1])[:300] if err else "(无 stderr)"


def probe_remote(remote: dict[str, Any], host: str, workstation: str, executable: str) -> str:
    """拉起前探测(同一 ssh 前缀): ssh 可达 + 远端工位目录存在 + harness 可执行在远端非交互 PATH。返回 "" = 通过, 否则拒因。"""
    import subprocess

    argv = remote_command(remote, host, _REMOTE_PROBE_SCRIPT, "lybra-probe", [workstation, executable])
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=float(remote["probe_timeout_seconds"]))
    except subprocess.TimeoutExpired:
        return f"ssh 探测 {host} 超时({remote['probe_timeout_seconds']}s)"
    except OSError as exc:
        return f"ssh 执行器 {remote['ssh_argv'][0]} 起不来: {type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return ""
    if proc.returncode == _SSH_UNREACHABLE:
        return f"ssh 不可达 {host}(exit 255): {_ssh_failure_text(proc)}"
    if proc.returncode == 3:
        return f"远端 {host} 工位目录不存在: {workstation}"
    if proc.returncode == 4:
        return f"harness 可执行 {executable} 不在远端 {host} 的非交互 ssh 会话 PATH"
    if proc.returncode == 127:
        return f"远端 {host} 无 {remote['remote_shell']}(exit 127)"
    return f"ssh 探测 {host} 失败(exit {proc.returncode}): {_ssh_failure_text(proc)}"


# 反向可达: $@ = 远端执行的 ssh argv(同一前缀 + 门机别名 + 门机侧检查命令); 远端 ssh exit 255 改报 86(与外层 ssh 自身 255 区分)
_REMOTE_REVERSE_SCRIPT = '"$@"\nrc=$?\n[ "$rc" -eq 255 ] && exit 86\nexit "$rc"\n'
_REVERSE_UNREACHABLE = 86


def remote_check_reverse(remote: dict[str, Any], host: str, gate_alias: str, governance_root: str) -> str:
    """AIPOS-F110 件③: 工位→门机反向可达检查——经 ssh 到工位, 由工位以同一 ssh 前缀连门机别名并 `test -d <门机治理根>`。
    返回 "" = 通过, 否则拒因(只读, 不写任何一端)。"""
    import subprocess

    inner = remote_command(remote, gate_alias, '[ -d "$1" ]\n', "lybra-gate-check", [governance_root])
    argv = remote_command(remote, host, _REMOTE_REVERSE_SCRIPT, "lybra-reverse", inner)
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=2 * float(remote["probe_timeout_seconds"]))
    except subprocess.TimeoutExpired:
        return f"反向检查超时({2 * float(remote['probe_timeout_seconds'])}s)"
    except OSError as exc:
        return f"ssh 执行器 {remote['ssh_argv'][0]} 起不来: {type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return ""
    if proc.returncode == _SSH_UNREACHABLE:
        return f"ssh 不可达 {host}(exit 255): {_ssh_failure_text(proc)}"
    if proc.returncode == _REVERSE_UNREACHABLE:
        return f"工位 {host} 经别名 {gate_alias} 连门机不可达: {_ssh_failure_text(proc)}"
    if proc.returncode == 1:
        return f"别名 {gate_alias} 所指机器上无门机治理根 {governance_root}(别名指错机器?)"
    return f"反向检查失败(exit {proc.returncode}): {_ssh_failure_text(proc)}"


def remote_signal(remote: dict[str, Any], host: str, pgid: int, signame: str) -> str:
    """经 ssh 对远端进程组发信号。返回 "sent" / "gone"(组已不存在)/ "failed: <原因>"(不吞, 由调用方记入收尾事件)。"""
    import subprocess

    argv = remote_command(remote, host, _REMOTE_SIGNAL_SCRIPT, "lybra-signal", [signame, str(int(pgid))])
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=float(remote["kill_timeout_seconds"]))
    except subprocess.TimeoutExpired:
        return f"failed: ssh {signame} 超时({remote['kill_timeout_seconds']}s)"
    except OSError as exc:
        return f"failed: {type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return "sent"
    if proc.returncode == 1:
        return "gone"
    if proc.returncode == 2:
        return f"failed: 远端组 {pgid} 在但 {signame} 发不出"
    return f"failed: ssh exit {proc.returncode}: {_ssh_failure_text(proc)}"


class LaunchedHarness:
    """一次拉起的 harness 进程(新进程组、无 shell)。pump = 注入 watch 的 sleeper(select 阻塞读输出, 非自旋)。

    local: stdin=/dev/null, cwd=工位目录。AIPOS-F110 remote(plan.remote 非空): 本地进程 = ssh(新进程组, cwd 不设),
    kickoff 写入其 stdin 后关闭; 构造时等远端组号行(pgid_marker)回传, 等不到 = 清本地 ssh 组并抛 RemoteLaunchError。"""

    _MAX_PARTIAL = 1 << 20

    def __init__(self, plan: Any, decl: dict[str, Any], say: Callable[[str], None]) -> None:
        import collections
        import selectors
        import subprocess

        self.plan, self.say = plan, say
        self.max_chars = int(decl["progress_max_chars"])
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=int(decl["stderr_tail_lines"]))
        self.counts: collections.Counter[str] = collections.Counter()
        self.remote: dict[str, Any] = dict(getattr(plan, "remote", None) or {})
        self.remote_pgid: int | None = None
        self._remote_decl = remote_declaration(decl) if self.remote else {}
        self.proc = subprocess.Popen(plan.argv, cwd=None if self.remote else plan.cwd,
                                     stdin=subprocess.PIPE if self.remote else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        self.pid = self.pgid = self.proc.pid  # start_new_session → setsid: 进程组号 = pid(remote = 本地 ssh 进程组)
        where = f" {self.remote.get('host')}" if self.remote else ""
        self.prefix = f"{plan.harness} {plan.card}{where} pid={self.pid}"
        self._sel = selectors.DefaultSelector()
        self._bufs: dict[str, bytes] = {}
        for name, stream in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr)):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                self._sel.register(stream, selectors.EVENT_READ, name)
                self._bufs[name] = b""
        if self.remote:
            self._remote_start(str(plan.kickoff), float(decl["terminate_wait_seconds"]))

    def _remote_start(self, kickoff: str, terminate_wait: float) -> None:
        failure = ""
        stdin = self.proc.stdin
        if stdin is None:
            failure = "ssh 子进程无 stdin 管道"
        else:
            try:
                stdin.write(kickoff.encode("utf-8"))  # kickoff 原字节, 只经 stdin(不进 ssh / 远端命令行)
                stdin.close()
            except OSError as exc:
                failure = f"kickoff 写入 ssh stdin 失败({type(exc).__name__}: {exc})"
        if not failure:
            self.pump(float(self._remote_decl["pgid_timeout_seconds"]), until=lambda: self.remote_pgid is not None)
            if self.remote_pgid is None:
                failure = (f"远端进程组号未回传(ssh exit {self.proc.returncode})" if self.exited()
                           else f"{self._remote_decl['pgid_timeout_seconds']}s 内未收到远端进程组号")
        if not failure:
            return
        self._terminate_local(terminate_wait)
        self.pump(terminate_wait)  # 读尽管道余量(EOF 即返回)再取 stderr 末尾
        self.close()
        rc = self.proc.returncode
        kind = "ssh 不可达" if rc == _SSH_UNREACHABLE else "跨机拉起失败"
        tail = " | ".join(self.stderr_tail) or "(stderr 无输出)"
        raise RemoteLaunchError(f"{kind} {self.remote.get('host')}: {failure}; stderr 末尾: {tail}")

    # -- 输出 --
    def _line(self, name: str, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip("\r")
        if name == "stderr":
            if text.strip():
                self.stderr_tail.append(redact_progress(text)[: self.max_chars])
            return
        if self.remote and self.remote_pgid is None:
            marker = str(self._remote_decl["pgid_marker"])
            if text.startswith(marker) and text[len(marker):].isdigit():
                self.remote_pgid = int(text[len(marker):])  # 远端组号行(首行)不计入事件
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

    def pump(self, seconds: float, until: Callable[[], bool] | None = None) -> None:
        """阻塞读输出至多 seconds 秒(select 事件驱动); 管道都关闭且进程已退出则提前返回。
        until(AIPOS-F110: 等远端组号行)= 每批输出处理后判定, 为真即返回。"""
        import subprocess

        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            if until is not None and until():
                return
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
        """终止整个进程组: SIGTERM → 等 wait_seconds → SIGKILL; 已退出则只清残留。返回处置说明。
        AIPOS-F110 remote: 经 ssh 对远端组 TERM → 等本地 ssh 退出至多 wait_seconds → 远端组 KILL(清残留), 再清本地 ssh 组;
        远端发信号失败不吞: 处置说明追加 remote_kill_failed(...)(进收尾事件)。"""
        if self.remote:
            return self._terminate_remote(wait_seconds)
        return self._terminate_local(wait_seconds)

    def _terminate_remote(self, wait_seconds: float) -> str:
        import subprocess

        how = "already_exited" if self.proc.poll() is not None else "sigterm"
        failures: list[str] = []
        if self.remote_pgid is not None:
            host = str(self.remote.get("host"))
            term = remote_signal(self._remote_decl, host, self.remote_pgid, "TERM")
            if term.startswith("failed"):
                failures.append(f"TERM {term}")
            elif term == "gone" and how == "sigterm":
                how = "remote_already_exited"
            try:
                self.proc.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                how = "sigkill"
            kill = remote_signal(self._remote_decl, host, self.remote_pgid, "KILL")  # 清远端组残留(含孙进程)
            if kill.startswith("failed"):
                failures.append(f"KILL {kill}")
        local = self._terminate_local(wait_seconds)
        if self.remote_pgid is None:
            how = f"{how}(远端组号未知, 只清本地 ssh: {local})"
        return how + (f"; remote_kill_failed({'; '.join(failures)})" if failures else "")

    def _terminate_local(self, wait_seconds: float) -> str:
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


def loop_signals() -> tuple[int, ...]:
    """AIPOS-F107 件①: loop 拉起期按「中断」处置的信号唯一声明 = SIGTERM/SIGINT/SIGHUP(ssh 断线 → SIGHUP, 与 SIGTERM 同语义)。"""
    import signal as _signal

    return (_signal.SIGTERM, _signal.SIGINT, _signal.SIGHUP)


@contextlib.contextmanager
def _installed(handler: Any) -> Any:
    """loop_signals() 装 handler、SIGPIPE 置忽略(写断开转 OSError, 由 TolerantOutput 丢弃), 退出上下文恢复原处置。非主线程不装。"""
    import signal as _signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {sig: _signal.signal(sig, handler) for sig in loop_signals()}
    previous[_signal.SIGPIPE] = _signal.signal(_signal.SIGPIPE, _signal.SIG_IGN)
    try:
        yield
    finally:
        for sig, old in previous.items():
            _signal.signal(sig, old)


@contextlib.contextmanager
def signals_raise_interrupt() -> Any:
    """拉起等待期间: loop_signals() → LoopInterrupted(调用方 finally 先清进程组)。非主线程不装(signal 只能主线程装)。"""

    def _handler(signum: int, _frame: Any) -> None:
        raise LoopInterrupted(signum)

    with _installed(_handler):
        yield


@contextlib.contextmanager
def signals_deferred() -> Any:
    """拉起/清进程组期间: loop_signals() 只记下(不打断), 退出上下文后恢复原处置; 调用方据记录再抛 LoopInterrupted。"""
    received: list[int] = []

    def _record(signum: int, _frame: Any) -> None:
        received.append(signum)

    with _installed(_record):
        yield received


class TolerantOutput:
    """AIPOS-F107 件①: loop 输出行的写出口。输出端断开(ssh 断线后终端写 EIO、管道读端关闭 BrokenPipeError, 均为 OSError)
    → 记下原因、把该流底层 fd 改指 /dev/null(后续写与解释器退出时 flush 都不再失败), 此后静默丢弃输出, 调用方照常清理与留痕。"""

    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.broken = ""

    def __call__(self, line: str) -> None:
        if self.broken:
            return
        try:
            print(line, file=self.stream, flush=True)
        except OSError as exc:
            self.broken = f"{type(exc).__name__}: {exc}"
            self._discard()

    def _discard(self) -> None:
        try:
            fd = self.stream.fileno()
        except (OSError, ValueError):
            return  # 无底层 fd(内存流)或已关闭: 只靠 broken 标记丢弃后续输出
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, fd)
        finally:
            os.close(devnull)


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
