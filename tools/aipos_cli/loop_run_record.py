"""AIPOS-F131 — loop 运行记录(件①)/ 日志落点(件③)/ `lybra loop status`(件②)。

一句话: `lybra loop` 把自己跑到哪(步骤)、拉起了什么(harness 进程)、进程最近一次有动静是何时何类、为何结束, 记成治理根里的一份
运行记录(record_type 读声明, 原子替换写); 人读输出同时写进记录旁的日志文件; `lybra loop status` 读记录 + 本机探活 + 判停滞,
顾问与 Owner 不再翻原始日志。

边界(gate-not-engine): 只记已发生的事实与进程存活, **不落会话正文与工具参数**——活动只记「类别 + 时间 + 计数」(工具名 / 发言 /
结束 / 出错), 日志里拉起进程的进度行只写类别, loop 自身输出行逐行过 harness_launch.redact_progress(凭据字样整行隐去, 长不透明串打码)。

声明(只读, 禁写死):
- 落点: project.json paths.loop_runs_root(config.schema configuration_sources.project_json.schema.paths, 唯一读取口
  workspace_config.project_paths; 缺省 5_tasks/records/loop_runs), 本卡运行在 <loop_runs_root>/<卡ID>/ 下;
- 记录形 / 节流 / 停滞阈值 / 结束原因 / 状态集: verbs.schema lybra_loop.run_record;
- 退出码: loop 的 outcome/exit_code 读 verbs.schema lybra_loop.exit_codes(loop_driver.exit_code_for); status 的读
  verbs.schema lybra_loop_status.exit_codes(verb_contract.declared_exit_code)。
判据单源: 运行状态(running / stalled / launch_dead / loop_dead / ended / unprobeable)只由 judge_run 判定, 调用方禁各自 grep 日志。

AIPOS-F136 件①: 顾问下一动作(continue_wait / owner_needed / card_done_take_next / investigate)只由 next_action 判定(判据表
verbs.schema lybra_loop_status.next_action); `loop status --wait <秒>` 只经 agent_watch_fs.run_fs_watch(loop 的唯一等待原语)
有界等待(wait_for_next_action), 上限与间隔读 verbs.schema lybra_loop_status.wait, 退出码按下一动作读同条 exit_codes。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tools.aipos_cli.clock import file_slug, iso_z, utc_now

RUN_RECORD_KEYS = ("record_type", "log_record_type", "activity_flush_seconds", "stall_after_seconds", "stall_after_tool_seconds",
                   "states", "end_reasons", "activity_categories", "background_category")
STATUS_VERB = "lybra_loop_status"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.:-]")


class LoopRunRecordError(RuntimeError):
    """运行记录建不起来 / 读不出(fail-closed: 调用方按声明出口处理, 禁吞)。"""


def run_record_declaration(contract: dict[str, Any]) -> dict[str, Any]:
    """verbs.schema lybra_loop.run_record。缺键 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = contract.get("run_record") if isinstance(contract, dict) else None
    if not isinstance(decl, dict) or any(k not in decl for k in RUN_RECORD_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_loop.run_record 未声明齐 {list(RUN_RECORD_KEYS)}")
    return decl


def loop_runs_root(governance_root: Path) -> Path:
    """本项目 loop 运行记录根(project.json paths.loop_runs_root, 唯一读取口 workspace_config.project_paths)。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(governance_root))["loop_runs_root"])


def loop_runs_dir(governance_root: Path, task_id: str) -> Path:
    from tools.aipos_cli.record_writer import validate_safe_task_id

    validate_safe_task_id(task_id)
    return loop_runs_root(governance_root) / task_id


# ---------------------------------------------------------------------------
# 探活(本机): pid 存在且进程启动时刻指纹与记录时一致(防 pid 复用)。
# AIPOS-F137(gap #118): 原以命令行(/proc/<pid>/cmdline)sha1 作指纹——pi(node)启动后改写进程标题(argv 覆写)致命令行变,
# 活进程被判 launch_dead, 顾问据此停 loop 连带终止在跑的审计 pi。现指纹 = 进程启动时刻(同一进程终身不变, 改标题 / 改名 /
# exec 都不变; pid 被复用的新进程启动时刻必不同)。启动时刻不含任何会话/命令内容, 原样落盘。
# 旧记录(F131 起至本卡前写的命令行 sha1 指纹, 16 位小写十六进制)兼容: 降级为「pid 存在即活」, status 标注「旧指纹, 仅按 pid」。
# ---------------------------------------------------------------------------

FINGERPRINT_PREFIX_PROC = "starttime:"  # Linux: /proc/<pid>/stat 第 22 字段(开机以来的时钟滴答数)
FINGERPRINT_PREFIX_PS = "lstart:"  # 无 /proc(macOS 等): `ps -o lstart=`(LC_ALL=C 固定格式, 空白归一为 _)
LEGACY_FINGERPRINT_NOTE = "旧指纹, 仅按 pid"
_LEGACY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")
_DEAD_STATES = (b"Z", b"X", b"x")  # 僵尸 / 已死: 进程已结束只差回收, 按不在计


def _proc_available() -> bool:
    """本机有无 /proc(探活读法二选一的唯一判定; 记录与探活同机同读法)。"""
    return Path("/proc/self").is_dir()


def _start_from_proc(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError as exc:
        raise LoopRunRecordError(f"读 /proc/{pid}/stat 无权限: {exc}") from exc
    if not raw.strip():
        return None  # 进程正在退出: 读到空 = 已不在
    close = raw.rfind(b")")  # 第 2 字段是括号包住的进程名, 可含空格与括号: 按最后一个右括号定位其后字段
    fields = raw[close + 1:].split() if close >= 0 else []
    if len(fields) < 20 or not fields[19].isdigit():
        raise LoopRunRecordError(f"/proc/{pid}/stat 形不合(右括号后应有 ≥20 个字段且第 22 字段 starttime 为整数): {raw[:120]!r}")
    if fields[0] in _DEAD_STATES:
        return None
    return FINGERPRINT_PREFIX_PROC + fields[19].decode("ascii")


def _start_from_ps(pid: int) -> str | None:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}  # lstart 随 locale 本地化(实测 zh_CN 出「四 10月 8 …」): 固定 C
    try:
        proc = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)], capture_output=True, timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LoopRunRecordError(f"ps 探活失败: {type(exc).__name__}: {exc}") from exc
    tokens = proc.stdout.split() if proc.returncode == 0 else []
    if len(tokens) < 2:
        return None  # ps 查无此 pid(退出码非 0 / 无输出)= 不在
    if tokens[0][:1] in _DEAD_STATES:
        return None
    return FINGERPRINT_PREFIX_PS + "_".join(t.decode("ascii", "replace") for t in tokens[1:])


def process_fingerprint(pid: int | None) -> str | None:
    """进程启动时刻指纹(唯一实现): Linux `starttime:<stat 第 22 字段>`; 无 /proc 用 `lstart:<ps -o lstart=>`。
    进程不存在 / 已成僵尸 = None。同一进程改标题 / 改名 / exec 指纹不变; pid 复用的新进程启动时刻不同 → 指纹不同。"""
    if not isinstance(pid, int) or pid <= 0:
        return None
    return _start_from_proc(pid) if _proc_available() else _start_from_ps(pid)


def fingerprint_is_legacy(fingerprint: Any) -> bool:
    """旧记录的命令行 sha1 指纹(16 位小写十六进制, 无前缀)。新指纹一律带前缀, 二者不相交。"""
    return isinstance(fingerprint, str) and bool(_LEGACY_FINGERPRINT_RE.match(fingerprint))


def process_alive(pid: Any, fingerprint: Any) -> bool:
    """记录的进程是否仍在(唯一判活)。新指纹: 现读启动时刻 == 记录值; 旧指纹: 降级为 pid 存在即活(无从核身, 调用方须标注
    LEGACY_FINGERPRINT_NOTE)。只读 /proc 或 ps, 不发任何信号。"""
    if not fingerprint or not isinstance(pid, int):
        return False
    current = process_fingerprint(pid)
    if current is None:
        return False
    return True if fingerprint_is_legacy(fingerprint) else current == fingerprint


def _parse_iso(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _safe_label(value: Any, limit: int = 64) -> str:
    return _SAFE_NAME_RE.sub("_", str(value or ""))[:limit] or "?"


# ---------------------------------------------------------------------------
# 件① + 件③ 写侧: 一次 loop 运行 = 一份运行记录(原子替换) + 一份日志(只追加)
# ---------------------------------------------------------------------------

class LoopRunRecorder:
    """一次 `lybra loop` 运行的记录器。构造即落盘(建不起来 = LoopRunRecordError, loop 以 not_derivable 出口拒跑, 不静默裸跑)。

    写入时机: 构造 / 每步开始(current_step)/ 每步落定(steps)/ 拉起开始与收尾 / 结束 —— 立即写;
    拉起进程活动 —— 首条立即写, 之后每 activity_flush_seconds 至多一次(flush() 由等待期 sleeper 每轮调用, 静默期也把最后一条落盘)。
    中途写失败: 不中断 loop(推进是主业), 但每种失败原因出声一次(warn 回调 = loop 输出)并计入 write_errors。"""

    def __init__(self, governance_root: Path, task_id: str, *, driver: str, contract: dict[str, Any],
                 warn: Callable[[str], None]) -> None:
        from tools.aipos_cli.record_writer import render_frontmatter_block

        self.decl = run_record_declaration(contract)
        self.warn = warn
        self.flush_every = float(self.decl["activity_flush_seconds"])
        self.started = utc_now()
        pid = os.getpid()
        try:
            self.dir = loop_runs_dir(governance_root, task_id)
            self.dir.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError) as exc:
            raise LoopRunRecordError(f"运行记录目录建不起来: {exc}") from exc
        self._log = None
        base = f"looprun_{task_id}_{file_slug('compact', self.started)}_{pid}"
        for n in range(1, 100):  # 同进程同秒再跑(夹具 / 立即重跑): 追加序号, 日志独占创建判撞, 绝不覆盖既有运行
            self.run_id = base if n == 1 else f"{base}_{n}"
            self.path, self.log_path = self.dir / f"{self.run_id}.md", self.dir / f"{self.run_id}.log"
            if self.path.exists():
                continue
            try:
                self._log = self.log_path.open("x", encoding="utf-8")
                break
            except FileExistsError:
                continue
            except OSError as exc:
                raise LoopRunRecordError(f"运行日志建不起来 {self.log_path}: {type(exc).__name__}: {exc}") from exc
        if self._log is None:
            raise LoopRunRecordError(f"运行记录名 {base}_* 已用尽(同秒 99 次), 拒跑")
        self.meta: dict[str, Any] = {
            "record_type": str(self.decl["record_type"]),
            "run_id": self.run_id,
            "task_id": task_id,
            "status": "running",
            "driver": driver,
            "envelope": None,
            "host": socket.gethostname(),
            "pid": pid,
            "process_fingerprint": process_fingerprint(pid),
            "started_at": iso_z(self.started),
            "updated_at": iso_z(self.started),
            "log_path": str(self.log_path),
            "current_step": None,
            "steps": [],
            "launches": [],
            "ended_at": None,
            "outcome": None,
            "exit_code": None,
            "end_reason": None,
            "end_message": None,
            "write_errors": 0,
        }
        self._synced = 0
        self._dirty = False
        self._last_flush = 0.0
        self._warned: set[str] = set()
        self._active: dict[str, Any] | None = None
        self._harness: Any = None
        try:
            header = render_frontmatter_block({"record_type": str(self.decl["log_record_type"]), "run_id": self.run_id,
                                               "task_id": task_id, "run_record": self.path.name},
                                              ["record_type", "run_id", "task_id", "run_record"])
            self._log.write(header + "\n")
            self._log.flush()
            self._write()
        except (OSError, ValueError) as exc:
            self.close_log()
            raise LoopRunRecordError(f"运行记录 / 日志写不进 {self.dir}: {type(exc).__name__}: {exc}") from exc

    # -- 日志(件③): loop 自身输出行逐行过 redact_progress; 拉起进程进度行只写类别(activity 内) --
    def log(self, text: str) -> None:
        from tools.aipos_cli.harness_launch import redact_progress

        if self._log is None:
            return
        try:
            stamp = iso_z()
            for line in str(text).splitlines() or [""]:
                self._log.write(f"{stamp} {redact_progress(line)}\n")
            self._log.flush()
        except (OSError, ValueError) as exc:
            self._failed("日志", exc)

    def close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError as exc:
                self._failed("日志关闭", exc)
            self._log = None

    # -- 记录写入 --
    def _failed(self, what: str, exc: BaseException) -> None:
        self.meta["write_errors"] = int(self.meta.get("write_errors") or 0) + 1
        key = f"{what}:{type(exc).__name__}"
        if key not in self._warned:
            self._warned.add(key)
            self.warn(f"warning — 运行记录{what}写入失败({type(exc).__name__}: {exc}); loop 照常推进, 进度以本输出为准")

    def _render(self) -> str:
        from tools.aipos_cli.record_writer import render_markdown

        m = self.meta
        cur = m.get("current_step") or {}
        lines = [f"# loop 运行 {m['run_id']}", "",
                 f"- 卡: {m['task_id']}; 驱动方: {m['driver']}; 信封: {m.get('envelope') or '(未定)'}",
                 f"- 主机: {m['host']}; loop pid: {m['pid']}; 开始: {m['started_at']}; 状态: {m['status']}",
                 f"- 当前步: {cur.get('index')} {cur.get('kind')} {cur.get('node')}/{cur.get('state')} {cur.get('card')}" if cur else "- 当前步: (无)",
                 f"- 已落定步数: {len(m['steps'])}; 拉起: {len(m['launches'])} 次",
                 f"- 日志: {m['log_path']}"]
        if m.get("ended_at"):
            lines.append(f"- 结束: {m['ended_at']} outcome={m['outcome']} exit={m['exit_code']} reason={m['end_reason']}")
        lines += ["", "只含已记录事实(步骤 / 进程 / 活动类别与时间 / 结束原因), 不含会话正文与工具参数。",
                  "查看: `lybra loop status --task-id <卡ID>`(读本记录 + 本机探活 + 判停滞)。"]
        return render_markdown(m, "\n".join(lines), list(m))

    def _write(self) -> None:
        self.meta["updated_at"] = iso_z()
        text = self._render()
        tmp = self.path.with_name(f".{self.path.name}.tmp{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)
        self._dirty = False
        self._last_flush = time.monotonic()

    def flush(self, force: bool = False) -> None:
        """有待写变更且(force 或距上次写 ≥ activity_flush_seconds)→ 原子替换写一次。"""
        if self._active is not None and self._harness is not None:
            self._snapshot_counts()
        if not self._dirty and not force:
            return
        if not force and time.monotonic() - self._last_flush < self.flush_every:
            return
        try:
            self._write()
        except (OSError, ValueError) as exc:
            self._failed("", exc)

    # -- 步骤 --
    def set_envelope(self, envelope: str) -> None:
        self.meta["envelope"] = envelope
        self._dirty = True
        self.flush(force=True)

    def step_started(self, index: int, kind: str, node: Any, state: Any, card: str, *, action: str = "",
                     waiting_for: list[str] | None = None, mode: str | None = None, hint: str | None = None) -> None:
        """mode/hint(AIPOS-F136 件①, 只用于等待步): launch = loop 已拉起执行引擎; manual = 手工模式等工位开工, hint = 开工提示
        (loop 输出的 manual 行同文, 经 redact_progress, 不含凭据)。"""
        from tools.aipos_cli.harness_launch import redact_progress

        self.meta["current_step"] = {"index": index, "kind": kind, "node": node, "state": state, "card": card,
                                     "action": action or None, "waiting_for": list(waiting_for or []) or None,
                                     "since": iso_z()}
        if mode:
            self.meta["current_step"]["mode"] = mode
        if hint:
            self.meta["current_step"]["hint"] = redact_progress(str(hint))[:240]
        self._dirty = True
        self.flush(force=True)

    def sync_steps(self, steps: list[Any]) -> None:
        """把 LoopResult.steps 里新落定的步追加进记录(只记结构化事实: 轮号/节点/动作/结果/时间, 不记命令与输出原文)。"""
        if len(steps) <= self._synced:
            return
        for step in steps[self._synced:]:
            launch = step.launch or {}
            self.meta["steps"].append({
                "index": step.index, "kind": step.kind, "node": step.node, "state": step.state, "card": step.card,
                "action": step.action_type or None, "ok": bool(step.ok), "exit_code": int(step.exit_code),
                "launched": bool(launch.get("launched")) if launch else None, "at": iso_z()})
        self._synced = len(steps)
        self._dirty = True
        self.flush(force=True)

    # -- 拉起 --
    def launch_started(self, plan: Any, harness: Any) -> None:
        self._harness = harness
        self._active = {
            "card": plan.card, "harness": plan.harness, "transport": plan.location.get("transport"),
            "host": plan.location.get("host"), "workstation": plan.cwd, "pid": harness.pid, "pgid": harness.pgid,
            "remote_pgid": harness.remote_pgid, "process_fingerprint": process_fingerprint(harness.pid),
            "started_at": iso_z(), "ended_at": None, "outcome": None, "returncode": None, "termination": None,
            "activity": {"events": 0, "last_event_at": None, "last_activity_at": None, "last_activity_kind": None,
                         "counts": {}},
        }
        self.meta["launches"].append(self._active)
        self.log(f"[launch] {plan.harness} {plan.card} pid={harness.pid} pgid={harness.pgid}")
        self._dirty = True
        self.flush(force=True)

    def _snapshot_counts(self) -> None:
        counts = {_safe_label(k): int(v) for k, v in sorted(dict(self._harness.counts).items())}
        if self._active is not None and counts != self._active["activity"]["counts"]:
            self._active["activity"]["counts"] = counts
            self._dirty = True

    def activity(self, category: str) -> None:
        """拉起进程的一行输出(stdout 事件 / stderr 行)。只记类别与时间; 进度日志行只写类别。"""
        if self._active is None:
            return
        act = self._active["activity"]
        first = act["events"] == 0
        now = iso_z()
        act["events"] += 1
        act["last_event_at"] = now
        label = _safe_label(category)
        if label != str(self.decl["background_category"]):  # 背景事件只刷新最近输出时间
            act["last_activity_at"], act["last_activity_kind"] = now, label
            self.log(f"  [{self._active['harness']} {self._active['card']} pid={self._active['pid']}] {label}")
        self._dirty = True
        self.flush(force=first)

    def launch_ended(self, outcome: str, returncode: Any, termination: str) -> None:
        if self._active is None:
            return
        if self._harness is not None:
            self._snapshot_counts()
        self._active.update({"ended_at": iso_z(), "outcome": outcome,
                             "returncode": returncode if isinstance(returncode, int) else None, "termination": termination})
        self._active = self._harness = None
        self._dirty = True
        self.flush(force=True)

    # -- 结束 --
    def end(self, *, outcome: str, exit_code: int, reason: str, message: str, steps: list[Any] | None = None) -> None:
        from tools.aipos_cli.harness_launch import redact_progress

        if steps is not None:
            self.sync_steps(steps)
        declared = self.decl["end_reasons"]
        if reason not in declared:
            raise LoopRunRecordError(f"结束原因 {reason!r} 未在 verbs.schema lybra_loop.run_record.end_reasons 声明")
        first = next((ln.strip() for ln in str(message or "").splitlines() if ln.strip()), "")
        self.meta.update({"status": "ended", "ended_at": iso_z(), "outcome": outcome, "exit_code": int(exit_code),
                          "end_reason": reason, "end_message": redact_progress(first)[:240] or None, "current_step": None})
        self.log(f"[end] outcome={outcome} exit={exit_code} reason={reason}")
        self.flush(force=True)
        self.close_log()


# ---------------------------------------------------------------------------
# 件② 读侧: 判据单源 judge_run + `lybra loop status`
# ---------------------------------------------------------------------------

def _seconds(a: datetime | None, b: datetime) -> int | None:
    return None if a is None else max(0, int((b - a).total_seconds()))


def judge_run(meta: dict[str, Any], decl: dict[str, Any], *, now: datetime | None = None,
              local_host: str | None = None) -> dict[str, Any]:
    """一份运行记录 → 状态视图(唯一判据)。
    ended: 记录已写结束; unprobeable: 记录写于别的主机(本机无从探活, 请在治理根所在机执行); loop_dead: loop 进程已不在而记录未结束;
    launch_dead: loop 在等、其拉起的进程已不在; stalled: 拉起的进程在但距最近一行输出(或拉起时刻)超过阈值(最近有意义活动是
    工具开始 tool:<名> = 工具在跑 → stall_after_tool_seconds, 否则 stall_after_seconds); running: 其余。
    「在 / 不在」一律经 process_alive(进程启动时刻指纹, AIPOS-F137); 记录是旧命令行指纹 = 仅按 pid 判, 视图带
    loop_probe_note / launch.probe_note = LEGACY_FINGERPRINT_NOTE。"""
    now = now or utc_now()
    host = str(meta.get("host") or "")
    local_host = local_host or socket.gethostname()
    stall_after = int(decl["stall_after_seconds"])
    started = _parse_iso(meta.get("started_at"))
    ended = _parse_iso(meta.get("ended_at")) if meta.get("status") == "ended" else None
    launches = [l for l in (meta.get("launches") or []) if isinstance(l, dict)]
    active = next((l for l in reversed(launches) if not l.get("ended_at")), None)
    view: dict[str, Any] = {
        "run_id": meta.get("run_id"), "task_id": meta.get("task_id"), "host": host, "driver": meta.get("driver"),
        "envelope": meta.get("envelope"), "pid": meta.get("pid"), "started_at": meta.get("started_at"),
        "elapsed_seconds": _seconds(started, ended or now), "current_step": meta.get("current_step"),
        "steps_done": len(meta.get("steps") or []), "log_path": meta.get("log_path"), "stall_after_seconds": stall_after,
        "loop_alive": None, "launch": None, "outcome": meta.get("outcome"), "exit_code": meta.get("exit_code"),
        "end_reason": meta.get("end_reason"), "end_message": meta.get("end_message"), "ended_at": meta.get("ended_at"),
    }
    last = launches[-1] if launches else None
    if last is not None:
        act = last.get("activity") or {}
        view["launch"] = {k: last.get(k) for k in ("card", "harness", "transport", "host", "workstation", "pid", "pgid",
                                                    "remote_pgid", "started_at", "ended_at", "outcome", "returncode", "termination")}
        view["launch"].update({"events": act.get("events"), "last_event_at": act.get("last_event_at"),
                               "last_activity_at": act.get("last_activity_at"), "last_activity_kind": act.get("last_activity_kind"),
                               "counts": act.get("counts"), "alive": None, "running_seconds": None, "idle_seconds": None})
    if meta.get("status") == "ended":
        view["state"] = "ended"
        return view
    if host != local_host:
        view["state"] = "unprobeable"
        view["note"] = f"记录写于主机 {host}, 本机 {local_host} 无从探活: 请在治理根所在机(经 ssh 亦可)执行 lybra loop status"
        return view
    view["loop_alive"] = process_alive(meta.get("pid"), meta.get("process_fingerprint"))
    if fingerprint_is_legacy(meta.get("process_fingerprint")):
        view["loop_probe_note"] = LEGACY_FINGERPRINT_NOTE
    if not view["loop_alive"]:
        view["state"] = "loop_dead"
        return view
    if active is not None and view["launch"] is not None:
        alive = process_alive(active.get("pid"), active.get("process_fingerprint"))
        if fingerprint_is_legacy(active.get("process_fingerprint")):
            view["launch"]["probe_note"] = LEGACY_FINGERPRINT_NOTE
        launched_at = _parse_iso(active.get("started_at"))
        last_seen = _parse_iso((active.get("activity") or {}).get("last_event_at")) or launched_at
        in_tool = str(view["launch"].get("last_activity_kind") or "").startswith("tool:")
        threshold = int(decl["stall_after_tool_seconds"]) if in_tool else stall_after
        view["stall_after_seconds"] = threshold
        view["launch"].update({"alive": alive, "running_seconds": _seconds(launched_at, now),
                               "idle_seconds": _seconds(last_seen, now), "tool_in_flight": in_tool})
        if not alive:
            view["state"] = "launch_dead"
        elif (view["launch"]["idle_seconds"] or 0) > threshold:
            view["state"] = "stalled"
        else:
            view["state"] = "running"
        return view
    view["state"] = "running"
    return view


def read_run(path: Path, decl: dict[str, Any]) -> dict[str, Any]:
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    try:
        meta, _body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        raise LoopRunRecordError(str(exc)) from exc
    if str(meta.get("record_type") or "") != str(decl["record_type"]):
        raise LoopRunRecordError(f"{path}: record_type={meta.get('record_type')!r} ≠ 声明 {decl['record_type']!r}"
                                 "(verbs.schema lybra_loop.run_record.record_type)")
    meta["_path"] = str(path)
    return meta


def _run_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("looprun_*.md") if p.is_file()) if directory.is_dir() else []


def _run_lane(governance_root: Path, task_id: str) -> dict[str, Any]:
    """AIPOS-F133 件①: 运行记录的 lane = 经 task_loader.find_task_card 反查卡面, 再走唯一派生 machine_zone.lane_of_card
    (运行记录不落 lane, 免第二份真相)。找不到卡/多义/卡面读不出 = 未解析 lane(带原因, 照列)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.machine_zone import lane_of_card
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card

    try:
        path, _state = find_task_card(Path(governance_root), task_id)
        if path is None:
            return {**lane_of_card(None, governance_root), "lane_error": f"队列中找不到卡 {task_id}, lane 无从解析"}
        fm, _body = require_frontmatter(path)
    except (AmbiguousTaskCard, FrontmatterReadError) as exc:
        return {**lane_of_card(None, governance_root), "lane_error": str(exc)}
    return lane_of_card(fm, governance_root)


def loop_status(governance_root: Path, task_id: str | None = None, *, now: datetime | None = None,
                contract: dict[str, Any] | None = None, lane: str | None = None) -> dict[str, Any]:
    """--task-id 给出 = 该卡最近一次运行(含已结束); 缺省 = 本项目全部未结束的运行(loop_dead 也列出: 记录未结束而进程已不在)。
    读不出的记录 = LoopRunRecordError(fail-closed, 点名文件)。
    AIPOS-F133 件②: 每个运行带 lane(_run_lane 反查卡面); lane 给出 = 经 machine_zone.filter_rows_by_lane 过滤(四命令同一函数)。"""
    from tools.aipos_cli.machine_zone import filter_rows_by_lane

    from tools.aipos_cli.loop_driver import load_loop_contract

    decl = run_record_declaration(contract or load_loop_contract())
    rules = next_action_declaration(decl)
    root = loop_runs_root(Path(governance_root))
    history: list[Any] = []
    if task_id:
        files = _run_files(loop_runs_dir(governance_root, task_id))
        metas = [read_run(p, decl) for p in files]
        metas.sort(key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
        picked = metas[-1:]
        total = len(metas)
        history = [m.get("end_reason") if m.get("status") == "ended" else None for m in metas[:-1]]
    else:
        picked = []
        for d in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
            picked += [m for m in (read_run(p, decl) for p in _run_files(d)) if m.get("status") != "ended"]
        total = len(picked)
    views = []
    for meta in picked:
        view = judge_run(meta, decl, now=now)
        view["record_path"] = meta["_path"]
        view.update(_run_lane(Path(governance_root), str(view.get("task_id") or meta.get("task_id") or "")))
        view["next_action"] = next_action(view, rules, governance_root=Path(governance_root),
                                          earlier_end_reasons=history if task_id else None)
        views.append(view)
    views = filter_rows_by_lane(views, lane)
    report = {"loop_runs_root": str(root), "task_id": task_id, "lane_filter": lane, "runs": views, "runs_on_record": total,
              "stall_after_seconds": int(decl["stall_after_seconds"]), "stall_after_tool_seconds": int(decl["stall_after_tool_seconds"])}
    if task_id:
        report["next_action"] = views[-1]["next_action"] if views else None
    return report


# ---------------------------------------------------------------------------
# AIPOS-F136 件①: 顾问下一动作(唯一判定)+ 有界等待(经 run_fs_watch)
# ---------------------------------------------------------------------------

NEXT_ACTIONS = ("continue_wait", "owner_needed", "card_done_take_next", "investigate")
_NEXT_ACTION_KEYS = ("actions", "by_state", "by_end_reason", "owner_reasons", "consecutive_failures_owner_needed",
                     "take_next_hint", "take_next_none_hint", "wait_hint", "investigate_hint")


def status_contract() -> dict[str, Any]:
    """verbs.schema lybra_loop_status 条目(唯一读取口 verb_contract 同源 schema_loader)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    contract = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(STATUS_VERB)
    if not isinstance(contract, dict):
        raise SchemaLoadError(f"verbs.schema.json verbs.{STATUS_VERB} 未声明")
    return contract


def next_action_declaration(run_decl: dict[str, Any], contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """verbs.schema lybra_loop_status.next_action, 并校验判据表覆盖 run_record 全部状态与结束原因(缺 = SchemaLoadError, fail-closed:
    新增状态 / 结束原因而判据表未跟 = 顾问拿不到下一动作, 宁拒不猜)。"""
    from tools.schema_loader import SchemaLoadError

    decl = (contract or status_contract()).get("next_action")
    if not isinstance(decl, dict) or any(k not in decl for k in _NEXT_ACTION_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.{STATUS_VERB}.next_action 未声明齐 {list(_NEXT_ACTION_KEYS)}")
    if set(decl["actions"]) != set(NEXT_ACTIONS):
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.actions 须恰为 {list(NEXT_ACTIONS)}, 得到 {sorted(decl['actions'])}")
    states = set(run_decl["states"]) - {"ended"}
    reasons = set(run_decl["end_reasons"])
    gaps = sorted(states - set(decl["by_state"])) + sorted(reasons - set(decl["by_end_reason"]))
    bad = sorted({v for v in list(decl["by_state"].values()) + list(decl["by_end_reason"].values())} - set(NEXT_ACTIONS))
    owner_gaps = sorted(r for r, a in decl["by_end_reason"].items() if a == "owner_needed" and r not in decl["owner_reasons"])
    if gaps or bad or owner_gaps or "consecutive_failures" not in decl["owner_reasons"]:
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action 判据表不全: 未覆盖 {gaps}; 非法动作 {bad}; "
                              f"owner_needed 缺事由 {owner_gaps + ([] if 'consecutive_failures' in decl['owner_reasons'] else ['consecutive_failures'])}"
                              f"(须覆盖 lybra_loop.run_record.states / end_reasons)")
    return decl


def next_action(view: dict[str, Any], rules: dict[str, Any], *, governance_root: Path,
                earlier_end_reasons: list[Any] | None = None) -> dict[str, Any]:
    """一个运行视图(judge_run)→ 顾问下一动作 {action, reason, detail, hint}(唯一判定, 判据表 verbs.schema next_action)。

    earlier_end_reasons = 本卡更早各次运行的结束原因(旧→新; 未结束 = None), 用于连败判定: 本次与其前连续
    consecutive_failures_owner_needed 次都以 investigate 类原因结束 = owner_needed(consecutive_failures)。"""
    task_id = str(view.get("task_id") or "")
    fill = {"task_id": task_id, "governance_root": str(governance_root)}
    state = str(view.get("state") or "")
    if state == "ended":
        reason = str(view.get("end_reason") or "")
        if reason not in rules["by_end_reason"]:
            raise LoopRunRecordError(f"{view.get('record_path') or view.get('run_id')}: 结束原因 {reason!r} 不在 verbs.schema "
                                     f"{STATUS_VERB}.next_action.by_end_reason(记录与声明不符, 拒判下一动作)")
        action = str(rules["by_end_reason"][reason])
        message = str(view.get("end_message") or "")
        if action == "investigate" and earlier_end_reasons is not None:
            streak = 1
            for earlier in reversed(earlier_end_reasons):
                if earlier is None or rules["by_end_reason"].get(str(earlier)) != "investigate":
                    break
                streak += 1
            if streak >= int(rules["consecutive_failures_owner_needed"]):
                return {"action": "owner_needed", "reason": "consecutive_failures",
                        "detail": f"{rules['owner_reasons']['consecutive_failures']}(连续 {streak} 次, 本次 {reason}: {message})",
                        "hint": str(rules["investigate_hint"]).format(**fill)}
        if action == "card_done_take_next":
            return {"action": action, "reason": reason, **_take_next(view, rules, governance_root, message)}
        if action == "owner_needed":
            return {"action": action, "reason": reason, "detail": f"{rules['owner_reasons'][reason]}" + (f" — {message}" if message else ""),
                    "hint": str(rules["investigate_hint"]).format(**fill)}
        return {"action": action, "reason": reason, "detail": message or reason, "hint": str(rules["investigate_hint"]).format(**fill)}
    if state not in rules["by_state"]:
        raise LoopRunRecordError(f"状态 {state!r} 不在 verbs.schema {STATUS_VERB}.next_action.by_state")
    action = str(rules["by_state"][state])
    if action == "continue_wait":
        cur = view.get("current_step") or {}
        if cur.get("kind") == "wait" and cur.get("mode") == "manual":
            what = cur.get("hint") or f"等工位产物 {cur.get('waiting_for')}"
            return {"action": action, "reason": "manual_kickoff", "detail": f"手工模式: {what}",
                    "hint": str(rules["wait_hint"]).format(**fill)}
        return {"action": action, "reason": state, "detail": "loop 推进中", "hint": str(rules["wait_hint"]).format(**fill)}
    detail = str(view.get("note") or "")
    if state == "stalled":
        detail = f"拉起进程 {(view.get('launch') or {}).get('idle_seconds')}s 无输出(阈值 {view.get('stall_after_seconds')}s)"
    elif state == "launch_dead":
        detail = "拉起的进程已不在而 loop 仍在等"
    elif state == "loop_dead":
        detail = "loop 进程已不在而运行记录未写结束"
    return {"action": action, "reason": state, "detail": detail or state, "hint": str(rules["investigate_hint"]).format(**fill)}


def _take_next(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, message: str) -> dict[str, Any]:
    """AIPOS-F136 × F133: 结案后取下一张 —— 接 F133「下一张」唯一出口(next_resolver.scan_project + pick_next_card, 判据
    verbs.schema lane_view.next_card), 范围 = 本卡 lane(machine_zone.lane_of_card 已解析时; 解析不了 = 全项目, 照 F133
    「不可证明不属本 lane 不隐藏」)。有下一张 = hint 为其 loop 命令(take_next_hint); 无 = hint 为 lane 扫描(take_next_none_hint)。"""
    from tools.aipos_cli.next_resolver import pick_next_card, scan_project

    lane = view.get("lane") if view.get("lane") and not view.get("lane_error") else None
    fill = {"task_id": str(view.get("task_id") or ""), "governance_root": str(governance_root),
            "lane_arg": f" --lane {lane}" if lane else ""}
    nxt = pick_next_card(scan_project(Path(governance_root), lane=lane))
    done = message or "已结案并落账"
    if nxt is None:
        return {"detail": f"{done}; 下一张可推进卡: 无{'(lane ' + lane + ')' if lane else ''}", "next_card": None,
                "hint": str(rules["take_next_none_hint"]).format(**fill)}
    card = {"task_id": nxt.get("task_id"), "lane": nxt.get("lane"), "priority": nxt.get("priority")}
    return {"detail": f"{done}; 下一张可推进卡: {card['task_id']} lane={card['lane'] or '-'} priority={card['priority'] or '-'}",
            "next_card": card, "hint": str(rules["take_next_hint"]).format(**{**fill, "next_task_id": card["task_id"]})}


class StatusUsageError(ValueError):
    """--wait 用法错(缺 --task-id / 秒数越界): 零等待, 出口 usage。"""


def wait_for_next_action(governance_root: Path, task_id: str, seconds: float, *, contract: dict[str, Any] | None = None,
                         sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                         interval: float | None = None, lane: str | None = None) -> dict[str, Any]:
    """`loop status --task-id <ID> --wait <秒>`: 经 agent_watch_fs.run_fs_watch(唯一等待原语)有界等待, 直到本卡最近一次运行的
    下一动作 ≠ continue_wait(expect_ready 每轮重读记录 + judge_run + next_action; 进程死 / 停滞无需文件变化)或到时; 返回当时的
    loop_status 报告 + wait 段 {requested_seconds, waited_seconds, outcome: ready|timeout}。上限与间隔读 verbs.schema。"""
    import contextlib
    import io
    from types import SimpleNamespace

    from tools.aipos_cli.agent_watch_fs import run_fs_watch
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.verb_contract import declared_exit_code

    wait_decl = status_contract().get("wait") or {}
    try:
        max_seconds, step = float(wait_decl["max_seconds"]), float(wait_decl["interval_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.schema.json verbs.{STATUS_VERB}.wait 未声明 max_seconds/interval_seconds: {exc}") from exc
    if not task_id:
        raise StatusUsageError("--wait 须同时给 --task-id(等的是这张卡最近一次 loop 运行)")
    if not (0 < float(seconds) <= max_seconds):
        raise StatusUsageError(f"--wait {seconds} 越界: 须 0 < 秒数 ≤ {max_seconds:g}(verbs.schema {STATUS_VERB}.wait.max_seconds)")
    contract = contract or load_loop_contract()
    governance_root = Path(governance_root)
    root = loop_runs_root(governance_root)
    loop_runs_dir(governance_root, task_id)  # 卡 ID 合法性(validate_safe_task_id), 与读记录同一口
    seen: dict[str, Any] = {}

    def _ready(_matched: list[str]) -> bool:
        report = loop_status(governance_root, task_id, contract=contract, lane=lane)
        seen["report"] = report
        action = (report.get("next_action") or {}).get("action")
        return action is not None and action != "continue_wait"

    started = clock()
    rc = None
    if root.is_dir():  # 落点根尚不存在 = 本项目从未跑过 loop: 无可等(no_run), 只读不建目录
        args = SimpleNamespace(workspace_root=str(root), expect=[f"{task_id}/looprun_*.md"], timeout=float(seconds),
                               interval=float(interval if interval is not None else step), events="expect", stream=False,
                               run_log=None, end_pattern=None, stall_secs=None, health=None)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = run_fs_watch(args, sleeper=sleeper, clock=clock, expect_ready=_ready)
        if rc not in (declared_exit_code("lybra_agent_watch", "change"), declared_exit_code("lybra_agent_watch", "timeout")):
            raise LoopRunRecordError(f"等待原语 agent watch 异常退出 {rc}: {sink.getvalue().strip()}")
    report = loop_status(governance_root, task_id, contract=contract, lane=lane)  # 返回当时状态(到时 / 就绪均重读一次, 不用缓存)
    outcome = "ready" if (report.get("next_action") or {}).get("action") not in (None, "continue_wait") else "timeout"
    report["wait"] = {"requested_seconds": float(seconds), "waited_seconds": round(clock() - started, 1), "outcome": outcome,
                      "watch_root": str(root), "max_seconds": max_seconds}
    return report


def _dur(seconds: Any) -> str:
    if not isinstance(seconds, int):
        return "?"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def render_status(report: dict[str, Any], decl_states: dict[str, Any]) -> str:
    runs = report["runs"]
    if not runs:
        what = f"卡 {report['task_id']} 无 loop 运行记录" if report.get("task_id") else "本项目无未结束的 loop 运行"
        if report.get("lane_filter"):
            what += f"(lane {report['lane_filter']})"
        return f"{what}(落点 {report['loop_runs_root']})"
    out: list[str] = []
    for v in runs:
        state = v["state"]
        out.append(f"loop 运行 {v['run_id']}  [{state}] {decl_states.get(state, '')}")
        out.append(f"  卡 {v['task_id']}  lane {v.get('lane') or '-'}  驱动 {v['driver']}  信封 {v.get('envelope') or '(未定)'}  主机 {v['host']}  "
                   f"loop pid {v['pid']}({'存活' if v['loop_alive'] else '已不在' if v['loop_alive'] is False else '未探活'}"
                   + (f"; {v['loop_probe_note']}" if v.get("loop_probe_note") else "") + ")")
        span = "历时" if state == "ended" else "已运行"
        out.append(f"  开始 {v['started_at']}({span} {_dur(v['elapsed_seconds'])})  已落定 {v['steps_done']} 步")
        cur = v.get("current_step")
        if cur and state != "ended":
            waiting = f" 等 {cur.get('waiting_for')}" if cur.get("waiting_for") else ""
            action = f" {cur.get('action')}" if cur.get("action") else ""
            out.append(f"  当前步 [{cur.get('index')}] {cur.get('kind')}{action} @ {cur.get('node')}/{cur.get('state')} "
                       f"({cur.get('card')}){waiting} 自 {cur.get('since')}")
        la = v.get("launch")
        if la:
            if la.get("ended_at"):
                out.append(f"  拉起 {la['harness']} {la['card']} pid={la['pid']} 已收尾: outcome={la.get('outcome')} "
                           f"returncode={la.get('returncode')} termination={la.get('termination')}")
            else:
                alive = {True: "存活", False: "已不在"}.get(la.get("alive"), "未探活")
                out.append(f"  拉起 {la['harness']} {la['card']} @ {la.get('host') or '本机'}:{la.get('workstation')} "
                           f"pid={la['pid']} pgid={la['pgid']} {alive}"
                           + (f"({la['probe_note']})" if la.get("probe_note") else "") + f", 已运行 {_dur(la.get('running_seconds'))}, "
                           f"距最近输出 {_dur(la.get('idle_seconds'))}")
            out.append(f"  活动: 事件 {la.get('events')} 条; 最近 {la.get('last_activity_kind') or '(无)'} @ "
                       f"{la.get('last_activity_at') or '-'}; 计数 {la.get('counts') or {}}")
        if state == "ended":
            out.append(f"  已结束 {v.get('ended_at')}: outcome={v.get('outcome')} exit={v.get('exit_code')} "
                       f"reason={v.get('end_reason')}" + (f" — {v['end_message']}" if v.get("end_message") else ""))
        if v.get("note"):
            out.append(f"  注: {v['note']}")
        if v.get("lane_error"):
            out.append(f"  lane: {v['lane_error']}")
        tool = (v.get("launch") or {}).get("tool_in_flight")
        out.append(f"  停滞判据: 拉起进程存活且 > {v['stall_after_seconds']}s 无输出 = stalled"
                   f"(verbs.schema lybra_loop.run_record.{'stall_after_tool_seconds, 工具在跑' if tool else 'stall_after_seconds'})")
        out.append(f"  记录 {v['record_path']}")
        out.append(f"  日志 {v['log_path']}")
        na = v.get("next_action")
        if na:
            out.append(f"  顾问下一动作: {na['action']}({na['reason']}) — {na['detail']}")
            out.append(f"    下一条: {na['hint']}")
    if report.get("task_id") and report.get("runs_on_record", 0) > 1:
        out.append(f"(该卡共 {report['runs_on_record']} 次运行记录, 上为最近一次)")
    w = report.get("wait")
    if w:
        out.append(f"(--wait {w['requested_seconds']:g}s: {'可行动' if w['outcome'] == 'ready' else '到时仍在推进'}, 实等 {w['waited_seconds']}s)")
    return "\n".join(out)


def loop_status_cli(args: Any) -> int:
    """`lybra loop status [--task-id <ID>] [--workspace-root <治理根>] [--json] [--wait <秒>]`。退出码读 verbs.schema
    lybra_loop_status.exit_codes(--wait 时按顾问下一动作)。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    from tools.aipos_cli.machine_zone import LaneFilterInvalid, lane_view_declaration, resolve_lane_filter

    task_id = getattr(args, "task_id", None)
    wait = getattr(args, "wait", None)
    try:
        governance_root = Path(getattr(args, "workspace_root", None) or _find_repo_root_for_args(args))
        # AIPOS-F133 件②: --lane 校验(不在声明 = 拒, 退出码读 verbs.schema lane_view.invalid_lane_exit_code)
        try:
            lane = resolve_lane_filter(governance_root, getattr(args, "lane", None))
        except LaneFilterInvalid as exc:
            print(f"lybra loop status: {exc}", file=sys.stderr)
            return int(lane_view_declaration()["invalid_lane_exit_code"])
        contract = load_loop_contract()
        # AIPOS-F138 件③: 信封判定诊断行([ENVELOPE_TRACE])只在 --verbose 时输出(「下一张」扫描会逐卡判信封); 判定逻辑不变
        from tools.aipos_cli.autonomy_policy import envelope_trace_output

        with envelope_trace_output(bool(getattr(args, "verbose", False))):
            if wait is None:
                report = loop_status(governance_root, task_id, contract=contract, lane=lane)
            else:
                report = wait_for_next_action(governance_root, task_id or "", float(wait), contract=contract, lane=lane)
    except StatusUsageError as exc:
        print(f"lybra loop status: {exc}", file=sys.stderr)
        return declared_exit_code(STATUS_VERB, "usage")
    except (FileNotFoundError, LoopRunRecordError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra loop status: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(STATUS_VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_status(report, run_record_declaration(contract)["states"]))
    if task_id and not report["runs"]:
        return declared_exit_code(STATUS_VERB, "no_run")
    if wait is not None:  # AIPOS-F136 件①: --wait 退出码按顾问下一动作(verbs.schema lybra_loop_status.exit_codes 同名出口)
        return declared_exit_code(STATUS_VERB, str(report["next_action"]["action"]))
    return declared_exit_code(STATUS_VERB, "ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
