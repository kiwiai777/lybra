"""AIPOS-F179 item 1: detached runner for long verification commands (`lybra run-long start|wait|status`).

Single implementation of verbs.schema `lybra_run_long`. Executors and auditors share it: a command that runs
longer than the agent's single bash tool call (full test suites, iOS UI suites, ...) is started detached
(new session via setsid, so tool timeouts / session teardown of the caller never reap it), its output goes to a
log file under the card's report location, and its exit code is written atomically when it ends. `wait` waits
in bounded segments through the single waiting primitive (agent_watch_fs.run_fs_watch) and reports progress.

Not a gate verb: no gate connection, no credentials, no records area. Paths come from project declarations
(next_resolver.card_report_path -> project.json paths.return_root / paths.verdict_root); nothing host specific.
Liveness = process start-time fingerprint (loop_run_record.process_alive, AIPOS-F137).
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERB = "lybra_run_long"

STATE_RUNNING = "running"
STATE_FINISHED = "finished"
STATE_LOST = "lost"
STATE_UNPROBEABLE = "unprobeable"


class RunLongError(ValueError):
    """Refusal with an operator-facing reason (exit code `refused`)."""


def declaration() -> dict[str, Any]:
    """verbs.schema lybra_run_long (read via the single verb-contract loader). Missing keys = SchemaLoadError."""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(VERB)
    needed = ("record", "shell_argv", "max_seconds_default", "timeout_exit_code", "term_grace_seconds", "wait", "exit_codes")
    if not isinstance(decl, dict) or any(k not in decl for k in needed):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB} 未声明齐 {list(needed)}")
    record = decl["record"]
    if any(not record.get(k) for k in ("dir", "record_type", "meta_file", "log_file", "exit_file", "result_file", "name_pattern")):
        raise SchemaLoadError(f"verbs.schema.json verbs.{VERB}.record 未声明齐")
    return decl


def exit_code(outcome: str) -> int:
    from tools.aipos_cli.verb_contract import declared_exit_code

    return declared_exit_code(VERB, outcome)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

def _card_frontmatter(governance_root: Path, task_id: str) -> dict[str, Any]:
    from tools.aipos_cli.next_resolver import _find_task_in_queue, _read_frontmatter

    path, _state = _find_task_in_queue(Path(governance_root), task_id)
    if path is None:
        raise RunLongError(f"队列中找不到卡 {task_id}(治理根 {governance_root}); 出口: 核对 --task-id 与全局 --workspace-root <治理根>")
    return _read_frontmatter(path)


def runs_root(governance_root: Path, task_id: str) -> Path:
    """<card report dir>/run_long (report dir = next_resolver.card_report_path parent; project declarations only)."""
    from tools.aipos_cli.next_resolver import card_report_path

    decl = declaration()
    fm = _card_frontmatter(governance_root, task_id)
    report_dir = card_report_path(Path(governance_root), task_id, fm).parent
    template = str(decl["record"]["dir"])
    probe = Path(template.replace("{report_dir}", str(report_dir)).replace("{name}", "__name__"))
    return probe.parent


def run_dir(governance_root: Path, task_id: str, name: str) -> Path:
    pattern = str(declaration()["record"]["name_pattern"])
    if not re.fullmatch(pattern, str(name or "")):
        raise RunLongError(f"运行名 {name!r} 不合(须匹配 {pattern}); 出口: 换一个只含字母数字 . _ - 的名字")
    return runs_root(governance_root, task_id) / str(name)


def _files(directory: Path) -> dict[str, Path]:
    record = declaration()["record"]
    return {key: directory / str(record[f"{key}_file"]) for key in ("meta", "log", "exit", "result")}


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunLongError(f"{path}: 读不出({type(exc).__name__}: {exc})") from exc
    if not isinstance(data, dict):
        raise RunLongError(f"{path}: 不是 JSON 对象")
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None = None) -> str:
    from tools.aipos_cli.clock import iso_z

    return iso_z(moment)


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def start_run(governance_root: Path, task_id: str, name: str, cwd: str, command: list[str], *,
              max_seconds: float | None = None) -> dict[str, Any]:
    """Create the run directory (name already used = refused, never overwritten), write meta, spawn the
    supervisor in a new session and return immediately."""
    from tools.aipos_cli.loop_run_record import process_fingerprint

    decl = declaration()
    argv_in = list(command or [])
    if argv_in and argv_in[0] == "--":
        argv_in = argv_in[1:]
    if not argv_in or not any(str(a).strip() for a in argv_in):
        raise RunLongError("缺命令: 用法 `lybra run-long start --task-id <卡> --name <名> --cwd <目录> -- <命令>`")
    workdir = Path(str(cwd or "")).expanduser()
    if not workdir.is_absolute() or not workdir.is_dir():
        raise RunLongError(f"--cwd 须为已存在目录的绝对路径, 实为 {cwd!r}")
    limit = float(decl["max_seconds_default"] if max_seconds is None else max_seconds)
    if limit <= 0:
        raise RunLongError(f"--max-seconds 须 > 0, 实为 {max_seconds!r}")
    directory = run_dir(governance_root, task_id, name)
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError as exc:
        raise RunLongError(f"运行名 {name!r} 已用({directory}); 出口: 换名重跑(绝不覆盖既有运行), 看结果用 "
                           f"`lybra run-long status --task-id {task_id} --name {name}`") from exc
    if len(argv_in) == 1:
        argv = [str(a) for a in decl["shell_argv"]] + [str(argv_in[0])]
        command_text = str(argv_in[0])
    else:
        argv = [str(a) for a in argv_in]
        command_text = shlex.join(argv)
    files = _files(directory)
    meta: dict[str, Any] = {
        "record_type": str(decl["record"]["record_type"]),
        "name": str(name),
        "task_id": str(task_id),
        "command": command_text,
        "argv": argv,
        "cwd": str(workdir.resolve()),
        "host": socket.gethostname(),
        "started_at": _iso(),
        "max_seconds": limit,
        "run_dir": str(directory),
        "log_path": str(files["log"]),
        "exit_path": str(files["exit"]),
        "result_path": str(files["result"]),
        "supervisor": None,
    }
    _write_json_atomic(files["meta"], meta)
    package_root = _package_root()
    env = {**os.environ, "PYTHONPATH": str(package_root)}
    with files["log"].open("ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.aipos_cli.run_long", "supervise", str(directory)],
            cwd=str(package_root), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
    meta["supervisor"] = {"pid": proc.pid, "process_fingerprint": process_fingerprint(proc.pid)}
    _write_json_atomic(files["meta"], meta)
    view = run_view(directory)
    view["wait_command"] = wait_command(governance_root, task_id, name)
    return view


def charter_commands(governance_root: Path) -> dict[str, str]:
    """Charter placeholders run_long_start / run_long_wait (single rendering; charter_render_context adds them).
    Governance root is the declared one; card id / name / work tree / command stay as angle-bracket placeholders."""
    seg = int(declaration()["wait"]["default_segment_seconds"])
    root = shlex.quote(str(governance_root))
    return {
        "run_long_start": f"`lybra --workspace-root {root} run-long start --task-id <本卡卡号> --name <运行名> --cwd <本卡工作树> -- '<验证命令>'`",
        "run_long_wait": f"`lybra --workspace-root {root} run-long wait --task-id <本卡卡号> --name <运行名> --timeout {seg}`",
    }


def wait_command(governance_root: Path, task_id: str, name: str) -> str:
    seg = int(declaration()["wait"]["default_segment_seconds"])
    parts = ["lybra", "--workspace-root", str(governance_root), "run-long", "wait", "--task-id", task_id, "--name", name,
             "--timeout", str(seg)]
    return " ".join(shlex.quote(p) for p in parts)


# ---------------------------------------------------------------------------
# supervisor (detached child; entry `python -m tools.aipos_cli.run_long supervise <dir>`)
# ---------------------------------------------------------------------------

def supervise(directory: Path) -> int:
    decl = declaration()
    files = _files(Path(directory))
    meta = _read_json(files["meta"])
    started = _now()
    timed_out = False
    signalled: list[int] = []
    rc: int
    with files["log"].open("ab") as log:
        try:
            child = subprocess.Popen([str(a) for a in meta["argv"]], cwd=str(meta["cwd"]), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, process_group=0)
        except OSError as exc:
            log.write(f"\n[run-long] 命令起不来: {type(exc).__name__}: {exc}\n".encode("utf-8"))
            child = None
            rc = 127
        if child is not None:
            def _forward(signum: int, _frame: Any) -> None:
                signalled.append(signum)
                _stop_group(child, float(decl["term_grace_seconds"]))

            signal.signal(signal.SIGTERM, _forward)
            signal.signal(signal.SIGINT, _forward)
            try:
                rc = child.wait(timeout=float(meta["max_seconds"]))
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_group(child, float(decl["term_grace_seconds"]))
                rc = int(decl["timeout_exit_code"])
                log.write(f"\n[run-long] 超过上限 {meta['max_seconds']}s, 已终止命令进程组\n".encode("utf-8"))
            if signalled and not timed_out:
                rc = 128 + signalled[0]
    finished = _now()
    result = {
        "finished_at": _iso(finished),
        "duration_seconds": int((finished - started).total_seconds()),
        "exit_code": int(rc),
        "timed_out": timed_out,
        "signal": signalled[0] if signalled else None,
        "log_sha256": _sha256(files["log"]),
    }
    _write_json_atomic(files["result"], result)
    _write_text_atomic(files["exit"], f"{int(rc)}\n")  # completion marker: written last
    return 0


def _stop_group(child: subprocess.Popen, grace: float) -> None:
    for sig, wait_for in ((signal.SIGTERM, grace), (signal.SIGKILL, None)):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            return
        try:
            child.wait(timeout=wait_for)
            return
        except subprocess.TimeoutExpired:
            continue


# ---------------------------------------------------------------------------
# read side: view / status / wait
# ---------------------------------------------------------------------------

def _tail_line(path: Path, limit: int = 200) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 4096))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


def run_view(directory: Path, *, local_host: str | None = None) -> dict[str, Any]:
    """One run directory -> state view (finished / running / lost / unprobeable). Read only."""
    from tools.aipos_cli.loop_run_record import process_alive

    files = _files(Path(directory))
    meta = _read_json(files["meta"])
    view: dict[str, Any] = {k: meta.get(k) for k in ("name", "task_id", "command", "cwd", "host", "started_at", "max_seconds",
                                                    "run_dir", "log_path", "exit_path", "result_path")}
    sup = meta.get("supervisor") or {}
    view["supervisor_pid"] = sup.get("pid")
    try:
        view["log_bytes"] = files["log"].stat().st_size
    except OSError:
        view["log_bytes"] = None
    view["log_last_line"] = _tail_line(files["log"])
    started = _parse_iso(meta.get("started_at"))
    if files["exit"].is_file():
        result = _read_json(files["result"]) if files["result"].is_file() else {}
        try:
            code = int(files["exit"].read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as exc:
            raise RunLongError(f"{files['exit']}: 退出码读不出({exc})") from exc
        view.update(state=STATE_FINISHED, exit_code=code, finished_at=result.get("finished_at"),
                    duration_seconds=result.get("duration_seconds"), timed_out=bool(result.get("timed_out")),
                    log_sha256=result.get("log_sha256"))
        return view
    view["elapsed_seconds"] = int((_now() - started).total_seconds()) if started else None
    if str(meta.get("host") or "") != (local_host or socket.gethostname()):
        view["state"] = STATE_UNPROBEABLE
        return view
    alive = process_alive(sup.get("pid"), sup.get("process_fingerprint"))
    view["state"] = STATE_RUNNING if alive else STATE_LOST
    return view


def _parse_iso(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def run_status(governance_root: Path, task_id: str, name: str | None = None) -> list[dict[str, Any]]:
    if name:
        directory = run_dir(governance_root, task_id, name)
        if not directory.is_dir():
            raise RunLongError(f"运行 {name!r} 不存在({directory})")
        return [run_view(directory)]
    root = runs_root(governance_root, task_id)
    meta_name = str(declaration()["record"]["meta_file"])
    return [run_view(d) for d in sorted(root.iterdir()) if (d / meta_name).is_file()] if root.is_dir() else []


def wait_run(governance_root: Path, task_id: str, name: str, timeout: float | None = None) -> dict[str, Any]:
    """Wait one bounded segment for the run to end (agent_watch_fs.run_fs_watch: expect = exit file, stop_when =
    supervisor gone). Returns the view plus `outcome` in {ok, still_running, lost}."""
    from tools.aipos_cli.agent_watch_fs import EXIT_CHANGE, EXIT_END_NO_PRODUCT, run_fs_watch
    from tools.aipos_cli.loop_driver import _watch_args
    from tools.aipos_cli.loop_run_record import process_alive

    decl = declaration()
    wait_decl = decl["wait"]
    segment = float(wait_decl["default_segment_seconds"] if timeout is None else timeout)
    if not 0 < segment <= float(wait_decl["max_segment_seconds"]):
        raise RunLongError(f"--timeout 须在 (0, {wait_decl['max_segment_seconds']}] 秒内(取本会话工具单次时限以内), 实为 {timeout!r}")
    directory = run_dir(governance_root, task_id, name)
    files = _files(directory)
    if not files["meta"].is_file():
        raise RunLongError(f"运行 {name!r} 不存在({directory}); 出口: 先 `lybra run-long start`")
    view = run_view(directory)
    if view["state"] == STATE_UNPROBEABLE:
        raise RunLongError(f"运行 {name!r} 起于主机 {view.get('host')}, 本机无从探活: 在该主机执行 wait")
    if view["state"] == STATE_RUNNING:
        sup = _read_json(files["meta"]).get("supervisor") or {}
        # the watch stops one interval before its bound, so the poll interval must stay well below the segment
        interval = min(float(wait_decl["interval_seconds"]), max(0.05, segment / 10))
        args = _watch_args(directory, [files["exit"].name], max_wait=segment, interval=interval)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = run_fs_watch(args, stop_when=lambda: not process_alive(sup.get("pid"), sup.get("process_fingerprint")))
        view = run_view(directory)
        if rc not in (EXIT_CHANGE, EXIT_END_NO_PRODUCT) and view["state"] == STATE_RUNNING:
            view["outcome"] = "still_running"
            view["next_wait"] = wait_command(governance_root, task_id, name)
            return view
    view["outcome"] = {STATE_FINISHED: "ok", STATE_LOST: "lost"}.get(view["state"], "still_running")
    if view["outcome"] == "still_running":
        view["next_wait"] = wait_command(governance_root, task_id, name)
    return view


# ---------------------------------------------------------------------------
# runtime evidence (F162 section) reference
# ---------------------------------------------------------------------------

def evidence_from_record(reference: str) -> tuple[dict[str, Any] | None, str]:
    """Resolve a run-long reference (run dir or its meta file, absolute) into the runtime-evidence entry fields
    {command, worktree, exit_code, log_path, log_sha256}. Returns (fields, "") or (None, reason)."""
    path = Path(str(reference or "")).expanduser()
    if not path.is_absolute():
        return None, f"run_long 引用须为运行目录的绝对路径, 实为 {reference!r}"
    try:
        decl = declaration()
        directory = path.parent if path.name == str(decl["record"]["meta_file"]) else path
        files = _files(directory)
        if not files["meta"].is_file():
            return None, f"run_long 引用 {reference} 不是 run-long 运行目录(缺 {files['meta'].name})"
        meta = _read_json(files["meta"])
        if str(meta.get("record_type") or "") != str(decl["record"]["record_type"]):
            return None, f"{files['meta']}: record_type={meta.get('record_type')!r} 不是 run-long 记录"
        if not files["exit"].is_file() or not files["result"].is_file():
            return None, f"run-long 运行 {directory.name} 未收尾(无退出码文件); 出口: `lybra run-long wait` 等到结束再引用"
        result = _read_json(files["result"])
        code = int(files["exit"].read_text(encoding="utf-8").strip())
        log = Path(str(meta.get("log_path") or ""))
        actual = _sha256(log)
    except (RunLongError, OSError, ValueError) as exc:
        return None, f"run_long 引用 {reference} 读不出: {exc}"
    recorded = str(result.get("log_sha256") or "").lower()
    if actual != recorded:
        return None, f"run-long 运行 {directory.name} 日志 {log} sha256={actual[:12]} ≠ 收尾时记录 {recorded[:12] or '缺'}(日志收尾后被改动)"
    return {"command": str(meta.get("command") or ""), "worktree": str(meta.get("cwd") or ""), "exit_code": code,
            "log_path": str(log), "log_sha256": actual}, ""


# ---------------------------------------------------------------------------
# rendering + CLI handler (thin shell in aipos_cli dispatches here)
# ---------------------------------------------------------------------------

def render_view(view: dict[str, Any]) -> list[str]:
    head = f"run-long {view.get('task_id')}/{view.get('name')}: {view.get('state')}"
    if view.get("state") == STATE_FINISHED:
        head += (f", 退出码 {view.get('exit_code')}" + (" (超上限被终止)" if view.get("timed_out") else "")
                 + f", 用时 {view.get('duration_seconds')}s")
    elif view.get("elapsed_seconds") is not None:
        head += f", 已运行 {view.get('elapsed_seconds')}s"
    lines = [head, f"  命令: {view.get('command')}", f"  cwd: {view.get('cwd')}",
             f"  日志: {view.get('log_path')}({view.get('log_bytes')} 字节)", f"  退出码文件: {view.get('exit_path')}"]
    if view.get("log_last_line"):
        lines.append(f"  日志末行: {view['log_last_line']}")
    if view.get("state") == STATE_LOST:
        lines.append("  supervisor 进程已不在而无退出码文件(机器重启 / 被杀): 结果不可得, 换名重跑")
    if view.get("next_wait"):
        lines.append(f"  仍在跑, 再等一段: {view['next_wait']}")
    if view.get("wait_command"):
        lines.append(f"  已脱离运行; 等结果(分段, 每段在工具时限内返回): {view['wait_command']}")
    if view.get("state") == STATE_FINISHED:
        lines.append(f"  运行证据可直接引用: {{\"run_long\": \"{view.get('run_dir')}\"}}")
    return lines


def cli(args: Any, governance_root: Path) -> int:
    """`lybra run-long` handler. Exit codes read from verbs.schema lybra_run_long.exit_codes."""
    sub = getattr(args, "run_long_command", None)
    try:
        if sub == "start":
            views = [start_run(governance_root, args.task_id, args.name, args.cwd, list(args.run_long_argv or []),
                               max_seconds=args.max_seconds)]
            outcome = "ok"
        elif sub == "wait":
            views = [wait_run(governance_root, args.task_id, args.name, args.timeout)]
            outcome = str(views[0]["outcome"])
        elif sub == "status":
            views = run_status(governance_root, args.task_id, args.name)
            outcome = "lost" if any(v.get("state") == STATE_LOST for v in views) and args.name else "ok"
        else:
            raise RunLongError("用法: lybra run-long start|wait|status --task-id <卡> ...")
    except RunLongError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return exit_code("refused")
    if getattr(args, "json", False):
        print(json.dumps(views[0] if sub != "status" else views, ensure_ascii=False, indent=2))
    else:
        if not views:
            print(f"run-long {args.task_id}: 无运行")
        for view in views:
            print("\n".join(render_view(view)))
    return exit_code(outcome)


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "supervise":
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        return supervise(Path(argv[1]))
    print("Error: tools.aipos_cli.run_long 只作 supervisor 入口(由 `lybra run-long start` 拉起); 请用 `lybra run-long`", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
