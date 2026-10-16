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
    summary, kind, _category = summarize_event_activity(line, fmt)
    return summary, kind


def summarize_event_activity(line: str, fmt: str) -> tuple[str | None, str, str]:
    """一行事件 → (一行式摘要 | None, 计数类别, 活动类别)。同一次解析(summarize_event 是其投影)。
    AIPOS-F131: 活动类别(verbs.schema lybra_loop.run_record.activity_categories)只含工具名 / 发言 / 结束 / 出错等类别,
    不含正文与参数——运行记录与日志只记它。"""
    if fmt != "pi-json":
        return None, "raw", "output"
    try:
        event = json.loads(line)
    except ValueError:
        return None, "non_json", "output"
    if not isinstance(event, dict):
        return None, "non_json", "output"
    kind = str(event.get("type") or "")
    if kind == "tool_execution_start":
        args = event.get("args")
        first = next(iter(args.values()), "") if isinstance(args, dict) and args else args
        return f"工具 {event.get('toolName')}: {_first_line(first)}", "summarized", f"tool:{event.get('toolName')}"
    if kind == "tool_execution_end":
        if event.get("isError"):
            result = event.get("result")
            if isinstance(result, dict) and isinstance(result.get("content"), list):
                result = " ".join(str(c.get("text") or "") for c in result["content"] if isinstance(c, dict))
            return f"工具 {event.get('toolName')} 出错: {_first_line(result)}", "summarized", f"tool_error:{event.get('toolName')}"
        return None, "quiet", f"tool_end:{event.get('toolName')}"
    if kind == "message_end":
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message.get("role") != "assistant":
            return None, "quiet", "stream"
        if message.get("stopReason") == "error" or message.get("errorMessage"):
            return f"错误: {_first_line(message.get('errorMessage') or 'stopReason=error')}", "summarized", "error"
        content = message.get("content")
        texts = [str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text"] if isinstance(content, list) else []
        first = _first_line("\n".join(texts))
        return (f"助手: {first}", "summarized", "assistant") if first else (None, "quiet", "assistant")
    if kind == "agent_end":
        return "agent 结束", "summarized", "agent_end"
    if kind in _PI_QUIET_EVENTS:
        return None, "quiet", "stream"
    return None, f"unknown:{kind or '?'}", "stream"


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


# ---------------------------------------------------------------------------
# AIPOS-F165 件③: 拉起禁提权(no_new_privs)。声明 verbs.schema lybra_loop.launch.privilege_guard; 开关 project.json launch_guard
# (workspace_config.project_launch_guard 唯一读取口)。判定唯一实现 privilege_guard_state(plan_launch 与 check-workstation 同读);
# 施加唯一处 = 下方 LaunchedHarness 的 Popen(本机: 子进程 exec 前 prctl, 内核标志随 fork/exec 继承且不可撤销)。
# ---------------------------------------------------------------------------

_PR_SET_NO_NEW_PRIVS = 38  # linux/prctl.h
_PR_GET_NO_NEW_PRIVS = 39
PRIVILEGE_GUARD_STATES = ("applied", "disabled", "degraded")


class PrivilegeGuardError(OSError):
    """应施加 no_new_privs 而施加不了(fail-closed: 不拉起)。调用方按「拉起失败」处置(exit 3 + 手工提示)。"""


def _prctl() -> Any:
    """libc prctl(仅 Linux); 不可用 = None。只在父进程解析(子进程 exec 前只调用, 不在 fork 后加载库)。"""
    import ctypes
    import sys

    if not sys.platform.startswith("linux"):
        return None
    try:
        func = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None
    func.restype = ctypes.c_int
    return func


def _prctl_args(option: int, arg2: int) -> tuple[Any, ...]:
    """prctl 变参按内核签名传 unsigned long(与 runall_isolation Landlock 启动器同一传法; 未用参数须为 0, 否则 EINVAL)。"""
    import ctypes

    return (ctypes.c_int(option), ctypes.c_ulong(arg2), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0))


def no_new_privs_supported() -> bool:
    """本机能否置 no_new_privs: prctl 可解析且 PR_GET_NO_NEW_PRIVS 返回 0/1(只读探测)。"""
    func = _prctl()
    return func is not None and func(*_prctl_args(_PR_GET_NO_NEW_PRIVS, 0)) in (0, 1)


def privilege_guard_declaration(decl: dict[str, Any]) -> dict[str, Any]:
    """verbs.schema lybra_loop.launch.privilege_guard(states / degraded_reasons)。缺 = ValueError(fail-closed)。"""
    guard = decl.get("privilege_guard") if isinstance(decl, dict) else None
    if not isinstance(guard, dict) or not isinstance(guard.get("states"), dict) \
            or any(not guard["states"].get(s) for s in PRIVILEGE_GUARD_STATES) \
            or not isinstance(guard.get("degraded_reasons"), dict) \
            or any(not guard["degraded_reasons"].get(k) for k in ("platform", "remote")):
        raise ValueError(f"verbs.schema.json lybra_loop.launch.privilege_guard 未声明齐 states{list(PRIVILEGE_GUARD_STATES)} / "
                         "degraded_reasons(platform/remote)")
    return guard


def privilege_guard_state(decl: dict[str, Any], governance_root: Any, transport: Any) -> dict[str, Any]:
    """一次拉起的禁提权判定(唯一实现): {state ∈ PRIVILEGE_GUARD_STATES, detail, owner_approval_ref}。
    开关读 project.json launch_guard(缺段 = 缺省开); 关闭 = disabled; 跨机 = degraded(remote); 本机平台不支持 = degraded(platform);
    其余 = applied。launch_guard 不合 = LaunchGuardDeclarationError 上抛(调用方拒拉起); project.json 不可读 = 原异常上抛。"""
    import sys

    from tools.aipos_cli.workspace_config import project_launch_guard

    texts = privilege_guard_declaration(decl)
    guard = project_launch_guard(governance_root)
    auth = guard["owner_approval_ref"]
    if not guard["no_new_privs"]:
        state, detail = "disabled", str(texts["states"]["disabled"]).replace("{owner_approval_ref}", str(auth))
    elif transport == "remote":
        state = "degraded"
        detail = str(texts["states"]["degraded"]).replace("{reason}", str(texts["degraded_reasons"]["remote"]))
    elif not no_new_privs_supported():
        reason = str(texts["degraded_reasons"]["platform"]).replace("{platform}", sys.platform)
        state, detail = "degraded", str(texts["states"]["degraded"]).replace("{reason}", reason)
    else:
        state, detail = "applied", str(texts["states"]["applied"])
    return {"state": state, "detail": detail, "owner_approval_ref": auth}


def _no_new_privs_preexec(func: Any) -> Callable[[], None]:
    """子进程 exec 前置 no_new_privs(失败 = 抛, Popen 在父进程报 SubprocessError → PrivilegeGuardError, 不拉起)。"""
    args = _prctl_args(_PR_SET_NO_NEW_PRIVS, 1)  # 父进程备好参数, 子进程 exec 前只调用

    def _apply() -> None:
        if func(*args) != 0:
            raise OSError("prctl(PR_SET_NO_NEW_PRIVS) 失败")
    return _apply


# ---------------------------------------------------------------------------
# AIPOS-F169: harness 模型参数与已配置模型清单。声明 enums.schema harness.<值>.launch.model; 拉起追加参数唯一处 = loop_driver.plan_launch
# (经 model_launch_args), 可用性核查唯一实现 = harness_model_catalog(`pi --list-models`; set-model 与 check-workstation 同读)。
# ---------------------------------------------------------------------------

_MODEL_KEYS = ("argv", "list_args", "list_timeout_seconds", "list_header_first_column", "id_template")


def model_declaration(template: dict[str, Any] | None, harness: str) -> dict[str, Any]:
    """enums.schema harness.<harness>.launch.model。缺 / 形不合 = ValueError(fail-closed)。"""
    decl = template.get("model") if isinstance(template, dict) else None
    if not isinstance(decl, dict) or any(k not in decl for k in _MODEL_KEYS):
        raise ValueError(f"enums.schema.json harness {harness} launch.model 未声明齐 {list(_MODEL_KEYS)}")
    argv, args = decl["argv"], decl["list_args"]
    if (not isinstance(argv, list) or argv.count("{model}") != 1 or not all(isinstance(a, str) and a for a in argv)
            or any("{" in a and a != "{model}" for a in argv)):
        raise ValueError(f"harness {harness} launch.model.argv 须为串数组且含且仅含一个整参数 {{model}}: {argv!r}")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) and a for a in args):
        raise ValueError(f"harness {harness} launch.model.list_args 须为非空串数组: {args!r}")
    if "{provider}" not in str(decl["id_template"]) or "{model}" not in str(decl["id_template"]):
        raise ValueError(f"harness {harness} launch.model.id_template 须含 {{provider}} 与 {{model}}: {decl['id_template']!r}")
    return decl


def model_launch_args(template: dict[str, Any] | None, harness: str, model: str) -> list[str]:
    """声明的模型 → 追加到拉起 argv 模板后的参数(launch.model.argv, {model} 整参数替换)。"""
    return [model if a == "{model}" else a for a in model_declaration(template, harness)["argv"]]


_SESSION_KEYS = ("argv", "id_prefix", "id_pattern")


def session_launch_args(template: dict[str, Any] | None, harness: str, card: str) -> tuple[str, list[str]] | None:
    """AIPOS-F171B 件②: enums.schema harness.<harness>.launch.session → (新会话 id, 追加到拉起 argv 的参数)。
    模板无 session 段 = None(不固定会话、不续接, 行为不变); 段形不合 / 生成的 id 不合 id_pattern = ValueError(fail-closed, 不拉起)。"""
    import secrets

    decl = template.get("session") if isinstance(template, dict) else None
    if decl is None:
        return None
    if not isinstance(decl, dict) or any(k not in decl for k in _SESSION_KEYS):
        raise ValueError(f"enums.schema.json harness {harness} launch.session 未声明齐 {list(_SESSION_KEYS)}")
    argv = decl["argv"]
    if (not isinstance(argv, list) or argv.count("{session_id}") != 1 or not all(isinstance(a, str) and a for a in argv)
            or any("{" in a and a != "{session_id}" for a in argv)):
        raise ValueError(f"harness {harness} launch.session.argv 须为串数组且含且仅含一个整参数 {{session_id}}: {argv!r}")
    slug = re.sub(r"[^a-z0-9]+", "-", str(card).lower()).strip("-") or "card"
    session_id = f"{decl['id_prefix']}-{slug}-{secrets.token_hex(6)}"
    if not re.fullmatch(str(decl["id_pattern"]), session_id):
        raise ValueError(f"harness {harness} 生成的会话 id {session_id!r} 不合 launch.session.id_pattern {decl['id_pattern']!r}")
    return session_id, [session_id if a == "{session_id}" else a for a in argv]


def harness_model_catalog(harness: str) -> dict[str, Any]:
    """本机该 harness 已配置的模型清单(唯一实现): 执行 [launch.argv[0], *launch.model.list_args](pi = `pi --list-models`, 只列不发请求),
    首个首列 = list_header_first_column 的行为表头, 其后每行前两列 → id_template。
    返回 {ok, models, command, detail}; 可执行不在 PATH / 超时 / 非零退出 / 无表头 = ok False(调用方按核不了处置, 不当可用)。"""
    import shutil
    import subprocess

    from tools.aipos_cli.autonomy_policy import launchable_harnesses

    template = launchable_harnesses().get(harness)
    decl = model_declaration(template, harness)
    exe = str(template["argv"][0]) if isinstance(template.get("argv"), list) and template["argv"] else ""
    command = [exe, *[str(a) for a in decl["list_args"]]]

    def fail(detail: str) -> dict[str, Any]:
        return {"ok": False, "models": [], "command": command, "detail": detail}

    if not exe or not shutil.which(exe):
        return fail(f"{exe or '(harness 可执行未声明)'} 不在本机 PATH")
    timeout = float(decl["list_timeout_seconds"])
    try:
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return fail(f"`{' '.join(command)}` 超时({timeout:g}s)")
    except OSError as exc:
        return fail(f"`{' '.join(command)}` 起不来: {type(exc).__name__}: {exc}")
    if proc.returncode != 0:
        tail = [redact_progress(ln)[:300] for ln in (proc.stderr or "").strip().splitlines()[-3:]]
        return fail(f"`{' '.join(command)}` exit {proc.returncode}: {' | '.join(tail) or '(无 stderr)'}")
    header = str(decl["list_header_first_column"])
    models: list[str] = []
    seen_header = False
    for line in (proc.stdout or "").splitlines():
        cols = line.split()
        if not cols:
            continue
        if not seen_header:
            seen_header = cols[0] == header
            continue
        if len(cols) >= 2:
            ident = str(decl["id_template"]).replace("{provider}", cols[0]).replace("{model}", cols[1])
            if ident not in models:
                models.append(ident)
    if not seen_header:
        return fail(f"`{' '.join(command)}` 输出无表头(首列 {header}), 无从解析")
    return {"ok": True, "models": models, "command": command, "detail": f"`{' '.join(command)}` 列出 {len(models)} 个已配置模型"}


def near_models(model: str, models: list[str], limit: int) -> list[str]:
    """不在清单的模型 → 相近可用模型(至多 limit 个): 同 id 不同 provider / 互为子串 优先, 再按 difflib 相似度。"""
    import difflib

    want = str(model).strip().lower()
    want_id = want.partition("/")[2] or want
    picked: list[str] = []
    for cand in models:
        cid = cand.lower().partition("/")[2]
        if cid == want_id or want_id in cid or (cid and cid in want_id):
            picked.append(cand)
    lowered = {c.lower(): c for c in models}
    for close in difflib.get_close_matches(want, list(lowered), n=limit, cutoff=0.6):
        if lowered[close] not in picked:
            picked.append(lowered[close])
    return picked[:limit]


class LaunchedHarness:
    """一次拉起的 harness 进程(新进程组、无 shell)。pump = 注入 watch 的 sleeper(select 阻塞读输出, 非自旋)。

    local: stdin=/dev/null, cwd=工位目录。AIPOS-F110 remote(plan.remote 非空): 本地进程 = ssh(新进程组, cwd 不设),
    kickoff 写入其 stdin 后关闭; 构造时等远端组号行(pgid_marker)回传, 等不到 = 清本地 ssh 组并抛 RemoteLaunchError。"""

    _MAX_PARTIAL = 1 << 20

    def __init__(self, plan: Any, decl: dict[str, Any], say: Callable[[str], None]) -> None:
        import collections
        import selectors
        import subprocess

        # AIPOS-F131: say 可带两个可选口(loop_driver._RunOutput 提供): progress = 一行式进度(含发言首行/工具首参, 只上 stdout,
        # 不进日志); activity = 每行输出的活动类别(只类别不含内容 → loop 运行记录)。缺省 = 进度走 say 本身、不记活动(行为同前)。
        self.plan, self.say = plan, getattr(say, "progress", say)
        self.on_activity: Callable[[str], None] | None = getattr(say, "activity", None)
        self.max_chars = int(decl["progress_max_chars"])
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=int(decl["stderr_tail_lines"]))
        self.counts: collections.Counter[str] = collections.Counter()
        self.remote: dict[str, Any] = dict(getattr(plan, "remote", None) or {})
        self.remote_pgid: int | None = None
        self._remote_decl = remote_declaration(decl) if self.remote else {}
        # AIPOS-F165 件③: 禁提权唯一施加处。判定读 plan.privilege_guard(plan_launch 经 privilege_guard_state 填); 未判定 = 按缺省开施加
        # (fail-closed); 本机 applied = exec 前 prctl, 施加不了 = PrivilegeGuardError 不拉起; disabled / degraded = 不施加(判定已记原因)
        guard = dict(getattr(plan, "privilege_guard", None) or {})
        self.privilege_guard = guard or (
            {"state": "degraded", "detail": "未经 plan_launch 判定的跨机拉起: 远端不施加", "owner_approval_ref": None} if self.remote
            else {"state": "applied", "detail": "未经 plan_launch 判定, 按缺省开施加", "owner_approval_ref": None})
        preexec = None
        if self.privilege_guard.get("state") == "applied" and not self.remote:
            func = _prctl()
            if func is None:
                raise PrivilegeGuardError("应施加 no_new_privs 而本机无 prctl, 不拉起(fail-closed)")
            preexec = _no_new_privs_preexec(func)
        try:
            self.proc = subprocess.Popen(plan.argv, cwd=None if self.remote else plan.cwd,
                                         stdin=subprocess.PIPE if self.remote else subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, start_new_session=True, close_fds=True, preexec_fn=preexec)
        except subprocess.SubprocessError as exc:  # preexec 失败(子进程未 exec 即退出)
            raise PrivilegeGuardError(f"no_new_privs 施加失败, 不拉起: {exc}") from exc
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
                if self.on_activity is not None:
                    self.on_activity("stderr")
            return
        if self.remote and self.remote_pgid is None:
            marker = str(self._remote_decl["pgid_marker"])
            if text.startswith(marker) and text[len(marker):].isdigit():
                self.remote_pgid = int(text[len(marker):])  # 远端组号行(首行)不计入事件
                return
        summary, kind, category = summarize_event_activity(text, self.plan.events)
        self.counts[kind] += 1
        if summary:
            self.say(f"  [{self.prefix}] {redact_progress(summary)[: self.max_chars]}")
        if self.on_activity is not None:
            self.on_activity(category)

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
