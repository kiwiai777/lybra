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

AIPOS-F150(gap #128): 门拒结束的运行另落结构化拒因码 end_codes(extract_end_codes, 抽取式样与适用结束原因读 verbs.schema
lybra_loop.run_record.end_codes; 只抽门拒原文里的既有错误码, 不造码); next_action 新增 advisor_fix —— end_codes 全部属例行清单
(verbs.schema lybra_loop_status.next_action.routine_rejections, 唯一声明)= 给出该码的固定修法(声明渲染), 否则 owner_needed 照旧;
同卡同一例行码连续 repeat_owner_needed 次 = owner_needed(routine_fix_exhausted)。

AIPOS-F152 件①: 运行记录 deploy 段 = 本次 loop 运行的代码(部署快照 + 提交 / 非快照不适用)与部署变化(changed_to / changed_at);
`loop status --wait` 等待中部署一变(本命令跑的是旧快照)= wait.outcome deploy_changed + 退出码 deploy_changed(判定唯一实现
deploy_runtime.DeployGuard, 经唯一等待原语 run_fs_watch 的 deploy_guard)。
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
                   "states", "end_reasons", "activity_categories", "background_category", "end_codes")
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


def extract_end_codes(message: Any, decl: dict[str, Any]) -> list[str]:
    """AIPOS-F150 件①: 门拒原文 → 既有门拒错误码列表(按首次出现顺序去重; 唯一实现)。式样读 verbs.schema
    lybra_loop.run_record.end_codes.patterns(每条含命名组 code); 声明缺 / 式样不合 = SchemaLoadError(fail-closed)。只抽不造码:
    原文里没有 `<码>:` / error_code=<码> 形 = 空列表(下一动作照旧 owner_needed)。"""
    from tools.schema_loader import SchemaLoadError

    spec = decl.get("end_codes") if isinstance(decl, dict) else None
    patterns = spec.get("patterns") if isinstance(spec, dict) else None
    if not isinstance(patterns, list) or not patterns:
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.run_record.end_codes.patterns 未声明(非空列表)")
    found: dict[int, str] = {}
    text = str(message or "")
    for raw in patterns:
        try:
            rx = re.compile(str(raw))
        except re.error as exc:
            raise SchemaLoadError(f"verbs.schema.json lybra_loop.run_record.end_codes.patterns 式样不合 {raw!r}: {exc}") from exc
        if "code" not in rx.groupindex:
            raise SchemaLoadError(f"verbs.schema.json lybra_loop.run_record.end_codes.patterns 式样缺命名组 code: {raw!r}")
        for m in rx.finditer(text):
            found.setdefault(m.start("code"), m.group("code"))
    codes: list[str] = []
    for _pos, code in sorted(found.items()):
        if code not in codes:
            codes.append(code)
    return codes


def end_codes_reasons(decl: dict[str, Any]) -> list[str]:
    """AIPOS-F150 件①: 落 end_codes 的结束原因(verbs.schema lybra_loop.run_record.end_codes.end_reasons)。须为已声明结束原因。"""
    from tools.schema_loader import SchemaLoadError

    spec = decl.get("end_codes") if isinstance(decl, dict) else None
    reasons = spec.get("end_reasons") if isinstance(spec, dict) else None
    if not isinstance(reasons, list) or not reasons or any(r not in decl.get("end_reasons", {}) for r in reasons):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.run_record.end_codes.end_reasons 未声明或含未声明的结束原因")
    return [str(r) for r in reasons]


def loop_runs_root(governance_root: Path) -> Path:
    """本项目 loop 运行记录根(project.json paths.loop_runs_root, 唯一读取口 workspace_config.project_paths)。"""
    from tools.aipos_cli.workspace_config import project_paths

    return Path(project_paths(Path(governance_root))["loop_runs_root"])


def loop_runs_dir(governance_root: Path, task_id: str) -> Path:
    from tools.aipos_cli.record_writer import validate_safe_task_id

    validate_safe_task_id(task_id)
    return loop_runs_root(governance_root) / task_id


def exclude_run_logs(directory: Path) -> None:
    """AIPOS-F147 件①(唯一实现): 运行记录旁的日志不入库——<loop_runs_root>/<卡ID>/*.log 经既有唯一实现 git_exclude.register_git_exclude
    登记进治理仓 .git/info/exclude(本地, 不提交; 一条文件模式 /<loop_runs_root 仓相对>/*/*.log, 幂等; 只排 .log, 运行记录 .md 照常落账)。
    directory = 某卡的运行记录目录。治理根不在 git 仓 = 跳过。AIPOS-F166 件③: 合并后回归日志同落此目录(post_merge_regression.regression_log_dir),
    同一条排除规则。登记失败 = OSError / ValueError / SubprocessError 原样抛(调用方出声)。"""
    from tools.aipos_cli.git_exclude import register_git_exclude

    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(directory), capture_output=True, text=True, timeout=10)
    if proc.returncode != 0 or not proc.stdout.strip():
        return  # 不在 git 仓: 无可排
    top = Path(proc.stdout.strip())
    rel = Path(directory).parent.resolve().relative_to(top.resolve()).as_posix()
    report = register_git_exclude(top, [f"/{rel}/*/*.log" if rel != "." else "/*/*.log"])
    if not report.get("ok"):
        raise OSError(str(report.get("error") or "register_git_exclude 未成功"))


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

def render_run_record(m: dict[str, Any]) -> str:
    """运行记录全文(frontmatter = meta 全量 + 人读摘要正文; 记录器与停线收口 / 复核同一渲染)。"""
    from tools.aipos_cli.record_writer import render_markdown

    m = {k: v for k, v in m.items() if not str(k).startswith("_")}  # read_run 附加的 _path 等不落盘
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


def _write_record(path: Path, meta: dict[str, Any]) -> None:
    """原子替换写运行记录(记录器与停线收口 / 复核同一写法)。"""
    text = render_run_record(meta)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
            "end_codes": None,
            "write_errors": 0,
        }
        self._synced = 0
        self._dirty = False
        self._last_flush = 0.0
        self._warned: set[str] = set()
        self._active: dict[str, Any] | None = None
        self._harness: Any = None
        self._sealed = False  # AIPOS-F166 件①: 已以 deploy_reexec 封存(本进程即将被新快照替换), 之后的 end() 不再改写
        self._stop_request: dict[str, Any] | None = None  # AIPOS-F231 件②: 停线请求(读到即缓存)
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
        self._exclude_logs()

    def _exclude_logs(self) -> None:
        """AIPOS-F147 件①: 日志不入库(唯一实现 exclude_run_logs)。登记失败不中断 loop, 出声一次(_failed)。"""
        try:
            exclude_run_logs(self.dir)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._failed("日志排除登记", exc)

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
        return render_run_record(self.meta)

    def _write(self) -> None:
        self.meta["updated_at"] = iso_z()
        _write_record(self.path, self.meta)
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

    # -- AIPOS-F166: 续等关联 / 信封用尽真相 --
    def set_continues(self, run_id: str) -> None:
        """件①: 本运行由上一运行在等待步遇部署更新 re-exec 而来(continues_run = 上一 run_id, 明确关联)。"""
        self.meta["continues_run"] = str(run_id)
        self._dirty = True
        self.flush(force=True)

    def set_envelope_state(self, state: dict[str, Any]) -> None:
        """件②: 信封用尽 / 过期时的门侧用量真相与续签命令(verbs.schema lybra_loop.run_record.envelope_state)。"""
        self.meta["envelope_state"] = {k: state.get(k) for k in ("policy_id", "state", "used", "max_tasks", "remaining", "expires_at",
                                                                  "next_policy_id", "renew_command", "note", "quota_line")}
        self._dirty = True
        self.flush(force=True)

    def reexec(self, change: dict[str, Any], *, target: Path) -> None:
        """件①: 等待步遇部署更新 → 本运行以 deploy_reexec 结束(outcome continued, exit_code null; reexec 段写新快照/提交/进程号), 封存
        (进程即被新快照替换, 同一 pid 的新运行记录以 continues_run 关联本 run_id)。结束原因名读声明 deploy_consistency.wait_reexec。"""
        from tools.aipos_cli.loop_driver import load_loop_contract

        reason = str(((load_loop_contract().get("deploy_consistency") or {}).get("wait_reexec") or {}).get("end_reason") or "")
        if reason not in self.decl["end_reasons"]:
            raise LoopRunRecordError(f"续等结束原因 {reason!r} 未在 verbs.schema lybra_loop.run_record.end_reasons 声明")
        self.deploy_changed(change)
        self.meta.update({"status": "ended", "ended_at": iso_z(), "outcome": "continued", "exit_code": None, "end_reason": reason,
                          "end_message": f"等待中部署已更新, 已 re-exec 到新部署快照 {Path(target).name} 从同一等待步续等",
                          "end_codes": None, "current_step": None,
                          "reexec": {"to_release": Path(target).name, "to_commit": change.get("to"), "at": iso_z(), "pid": os.getpid()}})
        self.log(f"[end] outcome=continued reason={reason} → {Path(target).name}(re-exec, pid {os.getpid()})")
        self.flush(force=True)
        self._sealed = True

    def reexec_failed(self, why: str) -> None:
        """件①: re-exec 没替换成进程 → 解封(调用方随后按 deploy_changed 写终态), reexec 段记失败原因。"""
        self._sealed = False
        self.meta["status"] = "running"
        self.meta.setdefault("reexec", {})["failed"] = str(why)[:240]
        self._dirty = True

    # -- 步骤 --
    def set_envelope(self, envelope: str) -> None:
        self.meta["envelope"] = envelope
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F152 件①: 部署一致性(verbs.schema lybra_loop.deploy_consistency.run_record_field) --
    def set_deploy(self, info: dict[str, Any]) -> None:
        """本次运行的代码: mode(deploy/dev/worktree)/ 快照 release / commit / deploy_current / deploy_check(active|not_applicable)。"""
        self.meta["deploy"] = dict(info)
        self._dirty = True
        self.flush(force=True)

    def set_env(self, env: dict[str, Any]) -> None:
        """AIPOS-F225 件②: 本次运行起跑时的环境指纹(env 段: validation_roots_digest; 部署提交另在 deploy 段), 自动重新派审的变化判据读它。"""
        self.meta["env"] = dict(env)
        self._dirty = True
        self.flush(force=True)

    def deploy_changed(self, change: dict[str, Any]) -> None:
        """部署已更新: 写明新旧提交(from = 本运行快照, to = 现部署 current; 读不出时 to_detail 给缘由)。"""
        deploy = dict(self.meta.get("deploy") or {})
        deploy.update({"changed_to": change.get("to"), "changed_to_detail": change.get("to_detail") or None,
                       "changed_at": iso_z()})
        self.meta["deploy"] = deploy
        self._dirty = True
        self.flush(force=True)

    def deploy_adopted(self, adopted: dict[str, Any]) -> None:
        """AIPOS-F155 件③: 本 loop 自身 finalize 的部署被采纳为比对基线(deploy.commit 仍 = 本进程运行快照; self_deployed_to = 采纳的提交)。"""
        deploy = dict(self.meta.get("deploy") or {})
        deploy.update({"self_deployed_to": adopted.get("to"), "self_deployed_at": iso_z()})
        self.meta["deploy"] = deploy
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
            # AIPOS-F155 件②: 等返工交回时提示另附返工节要点, 上限 240 → 600(子 agent 口径开工提示本身已近 240, 原上限截掉返工要点)
            self.meta["current_step"]["hint"] = redact_progress(str(hint))[:600]
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

    # -- AIPOS-F178 件③: 等待前缺树自动补建(verbs.schema lybra_loop.run_record.worktree_repairs) --
    def worktree_repair(self, entry: dict[str, Any]) -> None:
        """一次等待前缺树补建进运行记录 worktree_repairs(卡 / outcome / 工作树 / 修复命令 / detail / 时间; detail 经 redact_progress)。
        日志行由调用方的 loop 输出(say)写, 本方法不重复写日志。"""
        from tools.aipos_cli.harness_launch import redact_progress

        record = {k: entry.get(k) for k in ("card", "outcome", "worktree_path", "repair_command")}
        record["detail"] = redact_progress(str(entry.get("detail") or ""))[:600]
        record["at"] = iso_z()
        self.meta.setdefault("worktree_repairs", []).append(record)
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F187 件③: 结案后仍保留的工作树(verbs.schema lybra_loop.run_record.worktree_cleanups) --
    def worktree_cleanup(self, card: str, detail: str) -> None:
        """结案且已落账时本卡仍保留的工作树一句进运行记录 worktree_cleanups(卡 / detail 经 redact_progress / 时间)。
        日志行由调用方的 loop 输出(say)写。"""
        from tools.aipos_cli.harness_launch import redact_progress

        self.meta.setdefault("worktree_cleanups", []).append({"card": card, "detail": redact_progress(str(detail))[:600], "at": iso_z()})
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F188 件③: 起跑认领前的同仓车道重叠提示(verbs.schema lybra_loop.run_record.lane_overlaps) --
    def record_lane_overlaps(self, card: str, overlaps: list[dict[str, Any]]) -> None:
        """本卡认领前判出的车道重叠进运行记录 lane_overlaps 段: 对方卡 / 状态 / 仓 / 重叠路径 / 提示行 / 时间; loop status 显示。
        日志行由调用方的 loop 输出(say)写。"""
        at = iso_z()
        self.meta["lane_overlaps"] = [{"card": card, **{k: item.get(k) for k in ("other", "other_state", "repo", "paths", "line")}, "at": at}
                                      for item in overlaps]
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F180B item 2: report-header fix decisions (verbs.schema lybra_loop.run_record.report_fixes) --
    def report_fix(self, entry: dict[str, Any]) -> None:
        """One report-header fix decision into report_fixes (card / role / outcome / kinds / session_id / detail / at; detail via
        redact_progress; AIPOS-F204: role = auditor | executor). The log line is written by the caller's loop output (say)."""
        from tools.aipos_cli.harness_launch import redact_progress

        record = {k: entry.get(k) for k in ("card", "role", "outcome", "kinds", "session_id")}
        record["detail"] = redact_progress(str(entry.get("detail") or ""))[:600]
        record["at"] = iso_z()
        self.meta.setdefault("report_fixes", []).append(record)
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F164 件②: 拉起前章程判定(verbs.schema lybra_loop.run_record.charter_checks) --
    def charter_check(self, entry: dict[str, Any]) -> None:
        """一次拉起前章程判定进运行记录 charter_checks(卡 / 实例 / 工位 / outcome / detail / 修法命令 / 时间; detail 经 redact_progress)。
        日志行由调用方的 loop 输出(say)写, 本方法不重复写日志。"""
        from tools.aipos_cli.harness_launch import redact_progress

        record = {k: entry.get(k) for k in ("card", "instance", "workstation", "outcome", "repair_command")}
        record["detail"] = redact_progress(str(entry.get("detail") or ""))[:600]
        record["at"] = iso_z()
        self.meta.setdefault("charter_checks", []).append(record)
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F179 item 2: exclusive resource lock (verbs.schema lybra_loop.resource_lock; run_record.resource_lock) --
    def resource_request(self, resources: list[str], card: str) -> None:
        """Write the request (state=waiting) before judging; ordering key = (requested_at, run_id)."""
        self.meta["resource_lock"] = {"resources": list(resources), "card": card, "state": "waiting", "requested_at": iso_z(),
                                      "held_at": None, "holder": None}
        self._dirty = True
        self.flush(force=True)

    def resource_waiting(self, holder: dict[str, Any]) -> None:
        lock = self.meta.get("resource_lock") or {}
        view = {k: holder.get(k) for k in ("run_id", "task_id", "pid", "host", "state", "since", "resources")}
        if lock.get("holder") != view or lock.get("state") != "waiting":
            lock.update({"state": "waiting", "holder": view})
            self.meta["resource_lock"] = lock
            self._dirty = True
            self.flush(force=True)

    def resource_held(self) -> None:
        lock = self.meta.get("resource_lock") or {}
        lock.update({"state": "held", "held_at": iso_z(), "holder": None})
        self.meta["resource_lock"] = lock
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F231 件②: 协作式停线(verbs.schema lybra_loop.stop; run_record.stop) --
    def stop_request(self) -> dict[str, Any] | None:
        """本运行的停线请求(<run_id> + request_suffix; 每次只 stat, 出现后读一次并缓存)。请求读不出 = 仍按请求处理(停线优先, 原因记 note)。"""
        if self._stop_request is not None:
            return self._stop_request
        path = stop_request_path(self.path)
        if not path.exists():
            return None
        self._stop_request = read_stop_request(self.path)
        return self._stop_request

    def stop_saved(self, launches: list[dict[str, Any]], *, note: str = "") -> None:
        """先存现场(清理之前): 请求 + 各拉起快照(stop_snapshot)+ 当前步, 立即落盘。"""
        req = self.stop_request() or {}
        self.meta["stop"] = {**{k: req.get(k) for k in ("requested_at", "requested_by", "reason", "owner_words", "relayed_by", "timeout")},
                             "saved": {"at": iso_z(), "current_step": self.meta.get("current_step"), "launches": launches,
                                       "log_path": str(self.log_path), "note": note or None},
                             "cleaned": [], "residual": [], "outcome": None, "finished_at": None}
        self.log(f"[stop] 停线请求 @ {req.get('requested_at')} by {req.get('requested_by')}: 现场已保存({len(launches)} 次拉起)")
        self._dirty = True
        self.flush(force=True)

    def stop_finished(self, cleanup: dict[str, Any], outcome: str) -> None:
        """清理结果进 stop 段; outcome = stopped(无残留, 资源随结束释放)| incomplete(有残留: resource_lock.state = residual, 不释放)。"""
        stop = dict(self.meta.get("stop") or {})
        stop.update({"cleaned": list(cleanup.get("cleaned") or []), "residual": list(cleanup.get("residual") or []),
                     "outcome": outcome, "finished_at": iso_z()})
        self.meta["stop"] = stop
        lock = self.meta.get("resource_lock")
        if isinstance(lock, dict) and outcome == "incomplete":
            self.meta["resource_lock"] = {**lock, "state": "residual", "residual_at": iso_z()}
        self.log(f"[stop] 清理 {len(stop['cleaned'])} 项; 未清理 {len(stop['residual'])} 项; outcome={outcome}")
        self._dirty = True
        self.flush(force=True)

    # -- AIPOS-F231 件④⑤: 早退诊断 / 401·429 退避重试 / 开局体积 --
    def launch_diagnosis(self, diagnosis: dict[str, Any]) -> None:
        """最近一次拉起(已收尾)的早退诊断 launches[-1].diagnosis。"""
        if self.meta["launches"]:
            self.meta["launches"][-1]["diagnosis"] = dict(diagnosis)
            self._dirty = True
            self.flush(force=True)

    def provider_retry(self, entry: dict[str, Any]) -> None:
        self.meta.setdefault("provider_retries", []).append({**entry, "at": iso_z()})
        self.log(f"[retry] 模型服务 {entry.get('status')}({entry.get('kind')}) 退避 {entry.get('backoff_seconds')}s 后第 {entry.get('attempt')} 次重试")
        self._dirty = True
        self.flush(force=True)

    def context_check(self, entry: dict[str, Any]) -> None:
        self.meta.setdefault("context_checks", []).append({**entry, "at": iso_z()})
        self._dirty = True
        self.flush(force=True)

    # -- 拉起 --
    def launch_started(self, plan: Any, harness: Any, *, resume: int = 0, resume_why: str = "") -> None:
        """一次拉起(resume=0)或同会话第 resume 次续接(AIPOS-F171B 件②)= launches[] 追加一条; 续接另写日志一行 [resume]。
        AIPOS-F180B: resume_why = why this resume happened (default = the F171B half-way stop wording)."""
        self._harness = harness
        self._active = {
            "card": plan.card, "harness": plan.harness, "transport": plan.location.get("transport"),
            "host": plan.location.get("host"), "workstation": plan.cwd, "pid": harness.pid, "pgid": harness.pgid,
            "remote_pgid": harness.remote_pgid, "process_fingerprint": process_fingerprint(harness.pid),
            "privilege_guard": dict(getattr(harness, "privilege_guard", None) or {}),  # AIPOS-F165 件③: 禁提权实际状态(含降级原因)
            # AIPOS-F169: 实际传入的模型 {model(None = 未传 --model, pi 缺省), source, role}(verbs.schema run_record.launch_model)
            "model": dict(getattr(plan, "model", None) or {}) or None,
            # AIPOS-F171B 件②: 固定会话 id(无 session 声明 = null)/ 第几次续接 / 续接上限(verbs.schema run_record.launch_session)
            "session_id": getattr(plan, "session_id", "") or None, "resume": int(resume),
            "resume_max": int(getattr(plan, "resume_max", 0) or 0),
            "started_at": iso_z(), "ended_at": None, "outcome": None, "returncode": None, "termination": None,
            "activity": {"events": 0, "last_event_at": None, "last_activity_at": None, "last_activity_kind": None,
                         "counts": {}},
        }
        self.meta["launches"].append(self._active)
        if resume:
            self.log(f"[resume] {plan.harness} {plan.card} 会话 {self._active['session_id']} 续接 {resume}/{self._active['resume_max']}"
                     f"({resume_why or '上一轮 exit 0 而产物未就绪'})")
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

        if self._sealed:  # AIPOS-F166 件①: 已以 deploy_reexec 结束并交给新快照进程续等, 本记录终态不再改写
            return
        if steps is not None:
            self.sync_steps(steps)
        declared = self.decl["end_reasons"]
        if reason not in declared:
            raise LoopRunRecordError(f"结束原因 {reason!r} 未在 verbs.schema lybra_loop.run_record.end_reasons 声明")
        first = next((ln.strip() for ln in str(message or "").splitlines() if ln.strip()), "")
        # AIPOS-F150 件①: 门拒类结束另落结构化拒因码(取自门拒原文全文, 不只首行); 其余结束原因 = null
        codes = extract_end_codes(message, self.decl) if reason in end_codes_reasons(self.decl) else None
        ended_at = iso_z()
        self.meta.update({"status": "ended", "ended_at": ended_at, "outcome": outcome, "exit_code": int(exit_code),
                          "end_reason": reason, "end_message": redact_progress(first)[:240] or None, "end_codes": codes,
                          "current_step": None})
        lock = self.meta.get("resource_lock")
        if isinstance(lock, dict) and lock.get("state") in ("held", "waiting"):  # AIPOS-F231 件③: 运行结束 = 释放(停线残留 = residual 不动)
            self.meta["resource_lock"] = {**lock, "state": "released", "released_at": ended_at, "state_before_end": lock.get("state")}
        self.log(f"[end] outcome={outcome} exit={exit_code} reason={reason}" + (f" codes={','.join(codes)}" if codes else ""))
        self.flush(force=True)
        self.close_log()


# ---------------------------------------------------------------------------
# 件② 读侧: 判据单源 judge_run + `lybra loop status`
# ---------------------------------------------------------------------------

def _seconds(a: datetime | None, b: datetime) -> int | None:
    return None if a is None else max(0, int((b - a).total_seconds()))


def _last_wait_card(meta: dict[str, Any]) -> str | None:
    """AIPOS-F156 件①: 运行最后一次等待步的卡(当前步是等待 = 其卡; 否则已落定步中最后一个 kind=wait 的卡; 无 = None)。只读记录。"""
    cur = meta.get("current_step")
    if isinstance(cur, dict) and cur.get("kind") == "wait":
        return str(cur.get("card") or "") or None
    for step in reversed([s for s in (meta.get("steps") or []) if isinstance(s, dict)]):
        if step.get("kind") == "wait":
            return str(step.get("card") or "") or None
    return None


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
        "end_codes": [str(c) for c in (meta.get("end_codes") or [])],  # AIPOS-F150: 旧记录无此键 = 空列表
        "wait_card": _last_wait_card(meta),  # AIPOS-F156 件①: 最后一次等待的卡(执行方式分流只对本卡执行体等待生效)
        # AIPOS-F232 件②: 最后一个已落定步的卡(结束于报告读不出时报告所属卡: 审计报告挂审计卡)
        "last_step_card": next((str(st.get("card") or "") or None for st in reversed(meta.get("steps") or []) if isinstance(st, dict)), None),
    }
    last = launches[-1] if launches else None
    if last is not None:
        act = last.get("activity") or {}
        view["launch"] = {k: last.get(k) for k in ("card", "harness", "transport", "host", "workstation", "pid", "pgid",
                                                    "remote_pgid", "started_at", "ended_at", "outcome", "returncode", "termination",
                                                    "model", "session_id", "resume", "resume_max")}  # AIPOS-F169: model; AIPOS-F171B: 会话 / 续接(存量记录无 = None)
        view["launch"].update({"events": act.get("events"), "last_event_at": act.get("last_event_at"),
                               "last_activity_at": act.get("last_activity_at"), "last_activity_kind": act.get("last_activity_kind"),
                               "counts": act.get("counts"), "alive": None, "running_seconds": None, "idle_seconds": None})
    view["deploy"] = meta.get("deploy")  # AIPOS-F152 件①: 本次运行的代码与部署变化(旧记录无此键 = None)
    for key in ("continues_run", "reexec", "envelope_state", "owner_ask", "resource_lock",  # AIPOS-F166 / F168(无 = 不出现, 存量记录形不变)
                "stop", "provider_retries", "context_checks"):  # AIPOS-F231 件②④⑤
        if meta.get(key):
            view[key] = meta[key]
    if isinstance(view.get("owner_ask"), dict) and view["owner_ask"].get("retracted_at"):
        # AIPOS-F232 件①: 顾问已撤回的请求(lybra loop note --retract, 原请求留档)不再作 owner_ask(收件箱 / 下一动作同读), 改挂 owner_ask_retracted
        view["owner_ask_retracted"] = view.pop("owner_ask")
    if last is not None and isinstance(last.get("diagnosis"), dict):  # AIPOS-F231 件④: 最近一次拉起的早退诊断
        view["launch"]["diagnosis"] = last["diagnosis"]
    if meta.get("status") == "ended":
        view["state"] = "ended"
        lock = view.get("resource_lock")
        if isinstance(lock, dict) and lock.get("state") in ("held", "waiting"):
            # AIPOS-F231 件③: 已结束运行的锁 = 已随运行结束释放(存量记录 state 仍为 held 者同此; 停线残留 residual 照原样仍占用)
            view["resource_lock"] = {**lock, "state": "released", "released_at": lock.get("released_at") or meta.get("ended_at"),
                                     "state_before_end": lock.get("state")}
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


def latest_launch_session(governance_root: Path, task_id: str, card: str, *, own: dict[str, Any],
                          contract: dict[str, Any]) -> dict[str, Any] | None:
    """AIPOS-F180B item 2: the most recent fixed session the loop launched for `card` (launches[].session_id, F171B), this run's
    record first, then this task's earlier runs (newest first). Returns {session_id, workstation, run_id, launches} where
    launches = how many launches[] entries (all runs) used that session; None = no recorded session. Unreadable record = error."""
    decl = run_record_declaration(contract)
    metas = [own] + [read_run(p, decl) for p in reversed(_run_files(loop_runs_dir(governance_root, task_id)))
                     if str(p) != str(own.get("_path") or "") and p.stem != str(own.get("run_id") or "")]
    found: dict[str, Any] | None = None
    for meta in metas:
        for launch in reversed(list(meta.get("launches") or [])):
            if isinstance(launch, dict) and launch.get("card") == card and str(launch.get("session_id") or "").strip():
                found = {"session_id": str(launch["session_id"]), "workstation": launch.get("workstation"),
                         "run_id": meta.get("run_id")}
                break
        if found:
            break
    if found is None:
        return None
    found["launches"] = sum(1 for meta in metas for launch in (meta.get("launches") or [])
                            if isinstance(launch, dict) and launch.get("session_id") == found["session_id"])
    return found


def latest_charter_checks(governance_root: Path, *, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F174: 本项目各工位实例最近一次拉起前章程判定(F164 charter_check 写进运行记录 charter_checks 的原样条目)。
    看板「项目接入」页的「章程新鲜」项只读此记录真相(判定本身 = 拉起前 distribution_sync.charter_freshness, 已由 loop 记下;
    看板不重跑 sync --dry-run: 那会读工位凭据并连门)。返回 {checks: {实例: 最新条目(含 card / workstation / outcome / detail /
    repair_command / at)}, unreadable: [运行记录读不出的原因]}。无运行记录 = 空。只读。"""
    from tools.aipos_cli.loop_driver import load_loop_contract

    decl = run_record_declaration(contract or load_loop_contract())
    root = loop_runs_root(Path(governance_root))
    latest: dict[str, dict[str, Any]] = {}
    unreadable: list[str] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for path in _run_files(directory):
            try:
                meta = read_run(path, decl)
            except LoopRunRecordError as exc:
                unreadable.append(str(exc))
                continue
            for entry in meta.get("charter_checks") or []:
                if not isinstance(entry, dict) or not str(entry.get("instance") or "").strip():
                    continue
                name = str(entry["instance"]).strip()
                if name not in latest or str(entry.get("at") or "") >= str(latest[name].get("at") or ""):
                    latest[name] = dict(entry)
    return {"checks": latest, "unreadable": unreadable}


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


_SINGLE_INSTANCE_KEYS = ("end_reason", "live_states", "unprobeable_heartbeat_key", "message")


def single_instance_declaration(contract: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F171B 件①: verbs.schema lybra_loop.single_instance(同卡单实例)。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.schema_loader import SchemaLoadError

    decl = contract.get("single_instance") if isinstance(contract, dict) else None
    run_decl = run_record_declaration(contract)
    if not isinstance(decl, dict) or any(k not in decl for k in _SINGLE_INSTANCE_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_loop.single_instance 未声明齐 {list(_SINGLE_INSTANCE_KEYS)}")
    live = decl["live_states"]
    beat = run_decl.get(str(decl["unprobeable_heartbeat_key"]))
    if (decl["end_reason"] not in run_decl["end_reasons"] or decl["end_reason"] not in (contract.get("exit_codes") or {})
            or not isinstance(live, list) or not live or set(live) - (set(run_decl["states"]) - {"ended", "unprobeable"})
            or not isinstance(beat, int) or isinstance(beat, bool) or beat <= 0 or not isinstance(decl["message"], str)):
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.single_instance: end_reason 须为已声明结束原因且有同名退出码; "
                              "live_states ⊆ run_record.states(除 ended / unprobeable); unprobeable_heartbeat_key 须指向 run_record 的正整数秒数")
    return decl


def _step_label(step: Any) -> str:
    if not isinstance(step, dict):
        return "(无)"
    action = f" {step.get('action')}" if step.get("action") else ""
    return f"[{step.get('index')}] {step.get('kind')}{action} @ {step.get('node')}/{step.get('state')}({step.get('card')})"


def live_run_conflict(governance_root: Path, task_id: str, *, own: dict[str, Any], contract: dict[str, Any],
                      now: datetime | None = None, local_host: str | None = None) -> dict[str, Any] | None:
    """AIPOS-F171B 件①: 同卡单实例判定(唯一实现, loop 启动唯一口 loop_driver.run_loop 调用)。own = 本次运行记录 meta(已落盘)。
    本卡别的未结束运行中, (started_at, run_id) 先于本运行且判为存活的第一个 → {run_id, pid, host, started_at, current_step, state,
    heartbeat_age, message}; 无 = None。存活判据 = lybra loop status 同一判定 judge_run(本机进程启动时刻指纹, AIPOS-F137)的
    live_states; 别机记录(unprobeable)= 心跳 updated_at 距今 ≤ run_record.<unprobeable_heartbeat_key> 秒。先于本运行者才拒本运行
    (两个同时启动: 双方看到同一顺序, 先者继续、后者被拒)。运行记录读不出 = LoopRunRecordError(调用方 fail-closed 拒跑)。只读。"""
    decl = run_record_declaration(contract)
    si = single_instance_declaration(contract)
    now = now or utc_now()
    own_id = str(own.get("run_id") or "")
    own_key = (str(own.get("started_at") or ""), own_id)
    for path in _run_files(loop_runs_dir(Path(governance_root), task_id)):
        meta = read_run(path, decl)
        rid = str(meta.get("run_id") or "")
        if rid == own_id or meta.get("status") == "ended" or (str(meta.get("started_at") or ""), rid) >= own_key:
            continue
        view = judge_run(meta, decl, now=now, local_host=local_host)
        age = _seconds(_parse_iso(meta.get("updated_at")), now)
        if view["state"] == "unprobeable":
            live = age is not None and age <= int(decl[str(si["unprobeable_heartbeat_key"])])
        else:
            live = view["state"] in si["live_states"]
        if not live:
            continue
        fill = {"run_id": rid, "pid": meta.get("pid"), "host": meta.get("host"), "started_at": meta.get("started_at"),
                "current_step": _step_label(meta.get("current_step")), "task_id": task_id, "governance_root": str(governance_root)}
        message = str(si["message"])
        for key, value in fill.items():
            message = message.replace("{" + key + "}", str(value))
        if view["state"] == "unprobeable":
            message += f"(该运行记录写于主机 {meta.get('host')}, 本机无从探活; 按心跳判存活: 最近写入 {age}s 前)"
        return {**fill, "state": view["state"], "heartbeat_age": age, "message": message}
    return None


_RESOURCE_LOCK_KEYS = ("card_field", "enum", "wait_kind", "end_reason", "waiting_message", "acquired_message")


def resource_lock_declaration(contract: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F179 item 2: verbs.schema lybra_loop.resource_lock. Missing / malformed = SchemaLoadError (fail-closed)."""
    from tools.schema_loader import SchemaLoadError

    decl = contract.get("resource_lock") if isinstance(contract, dict) else None
    run_decl = run_record_declaration(contract)
    if not isinstance(decl, dict) or any(not decl.get(k) for k in _RESOURCE_LOCK_KEYS):
        raise SchemaLoadError(f"verbs.schema.json verbs.lybra_loop.resource_lock 未声明齐 {list(_RESOURCE_LOCK_KEYS)}")
    if decl["end_reason"] not in run_decl["end_reasons"]:
        raise SchemaLoadError("verbs.schema.json verbs.lybra_loop.resource_lock.end_reason 须为 run_record.end_reasons 已声明者")
    return decl


def card_resources(card_frontmatter: dict[str, Any] | None, *, contract: dict[str, Any] | None = None) -> list[str]:
    """AIPOS-F179 item 2: the card's exclusive resources (card.schema fields.resources; values = enums.schema
    exclusive_resource). Single shape check (draft_validator and the loop call this). Absent = []. Malformed =
    ValueError starting with CARD_RESOURCES_INVALID."""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.schema_loader import get_enum_values

    decl = resource_lock_declaration(contract or load_loop_contract())
    value = (card_frontmatter or {}).get(str(decl["card_field"])) if isinstance(card_frontmatter, dict) else None
    if value is None or value == []:
        return []
    allowed = get_enum_values(str(decl["enum"]))
    if not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value):
        raise ValueError(f"CARD_RESOURCES_INVALID: 卡面 {decl['card_field']} 须为字符串列表, 每项 ∈ enums.schema {decl['enum']} {allowed}, "
                         f"实为 {value!r}; 出口: 卡稿写 \"{decl['card_field']}\": [\"{allowed[0]}\"]")
    out: list[str] = []
    for item in value:
        if item not in out:
            out.append(item)
    return out


def resource_wait_line(holder: dict[str, Any], lock_decl: dict[str, Any]) -> str:
    """Holder line (resource_lock.waiting_message) - one rendering for the loop output, run record hint and loop status detail."""
    fill = {"resources": ", ".join(holder.get("resources") or []), "holder_task_id": holder.get("task_id"),
            "holder_run_id": holder.get("run_id"), "holder_pid": holder.get("pid"), "holder_host": holder.get("host"),
            "holder_state": "持有中" if holder.get("state") == "held" else "排在前面", "holder_since": holder.get("since")}
    message = str(lock_decl["waiting_message"])
    for key, value in fill.items():
        message = message.replace("{" + key + "}", str(value))
    return message


def resource_conflict(governance_root: Path, own: dict[str, Any], *, contract: dict[str, Any], now: datetime | None = None,
                      local_host: str | None = None) -> dict[str, Any] | None:
    """AIPOS-F179 item 2: the single resource-lock judgement (loop_driver calls it at the launch entry, next to
    live_run_conflict). own = this run's record meta with resource_lock already written. Scans every card's run
    records under paths.loop_runs_root; another run blocks us when it is alive (judge_run live_states, or the
    unprobeable heartbeat rule of single_instance), shares a resource, and either holds it, or waits with an
    earlier (requested_at, run_id) while we do not hold it yet. Holders win over waiters in the returned view.
    Unreadable records = LoopRunRecordError (caller fails closed). Read only."""
    decl = run_record_declaration(contract)
    si = single_instance_declaration(contract)
    lock_decl = resource_lock_declaration(contract)
    now = now or utc_now()
    lock = own.get("resource_lock") or {}
    mine = set(lock.get("resources") or [])
    if not mine:
        return None
    own_id = str(own.get("run_id") or "")
    own_key = (str(lock.get("requested_at") or ""), own_id)
    own_held = lock.get("state") == "held"
    root = loop_runs_root(Path(governance_root))
    found: list[tuple[int, dict[str, Any]]] = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for path in _run_files(directory):
            meta = read_run(path, decl)
            rid = str(meta.get("run_id") or "")
            other = meta.get("resource_lock") if isinstance(meta.get("resource_lock"), dict) else None
            # AIPOS-F231 件②: 停线未清干净的运行(resource_lock.state = residual)结束了也仍算占用(残留可能还在用资源), 不论进程存活
            residual = bool(other) and other.get("state") == "residual"
            if rid == own_id or (meta.get("status") == "ended" and not residual) or not other:
                continue
            shared = sorted(mine & set(other.get("resources") or []))
            state = "held" if residual else str(other.get("state") or "")
            if not shared or state not in ("waiting", "held"):
                continue
            other_key = (str(other.get("requested_at") or ""), rid)
            if state == "held" and own_held and own_key < other_key:
                continue
            if state == "waiting" and (own_held or other_key > own_key):
                continue
            view = judge_run(meta, decl, now=now, local_host=local_host)
            age = _seconds(_parse_iso(meta.get("updated_at")), now)
            if residual:
                live = True
            elif view["state"] == "unprobeable":
                live = age is not None and age <= int(decl[str(si["unprobeable_heartbeat_key"])])
            else:
                live = view["state"] in si["live_states"]
            if not live:
                continue
            since = other.get("held_at") if state == "held" else other.get("requested_at")
            message = resource_wait_line({"run_id": rid, "task_id": meta.get("task_id"), "pid": meta.get("pid"), "host": meta.get("host"),
                                          "state": state, "since": since, "resources": shared}, lock_decl)
            found.append((0 if state == "held" else 1, {"run_id": rid, "task_id": meta.get("task_id"), "pid": meta.get("pid"),
                                                         "host": meta.get("host"), "state": state, "resources": shared, "since": since,
                                                         "run_state": view["state"], "message": message}))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[0][1]


# ---------------------------------------------------------------------------
# AIPOS-F231 件④: 拉起早退诊断——读该次拉起固定会话的会话记录(定位器 card.schema intent_face.harness.session_record_locators,
# 唯一读取口 artifact_ingest._session_locators; 声明 verbs.schema lybra_loop.launch.early_exit)。放在本模块(finalize 预载闭包不下探)而非 harness_launch, 免把 artifact_ingest 拉进合并后预载。只读, 只取停止原因 / 错误首段 / provider / 模型 /
# 输入词元数(不取会话正文)。
# ---------------------------------------------------------------------------

def session_diagnosis(harness: str, session_id: str, early: dict[str, Any]) -> dict[str, Any]:
    """→ {kind, stop_reason, status, provider, model, input_size(本轮输入词元数), error_excerpt, session_file, note}。kind ∈ early.kinds 的键:
    stopReason = length_stop_reason → context_overflow; = error_stop_reason 且 errorMessage 首段状态码 ∈ retry_statuses → 该类(credential / quota),
    其余错误 → provider_error; 会话记录找不到 / 无助手消息 / 定位器未声明诊断键 → unknown(note 写明)。"""
    import glob as _glob

    import json

    from tools.aipos_cli.artifact_ingest import _dig, _session_locators
    from tools.aipos_cli.harness_launch import redact_progress

    out: dict[str, Any] = {"kind": "unknown", "stop_reason": None, "status": None, "provider": None, "model": None, "input_size": None,
                           "error_excerpt": None, "session_file": None, "note": None}
    locator = _session_locators().get(harness) if harness else None
    if not isinstance(locator, dict) or not locator.get("stop_reason_path") or not locator.get("session_id_file_suffix"):
        out["note"] = f"harness {harness or '?'} 的会话定位器未声明诊断键(card.schema session_record_locators)"
        return out
    if not session_id:
        out["note"] = "本次拉起未固定会话 id, 会话记录无从定位"
        return out
    suffix = str(locator["session_id_file_suffix"]).replace("{session_id}", session_id)
    files = sorted({f for pattern in locator.get("session_globs") or [] for f in _glob.glob(os.path.expanduser(str(pattern))) if f.endswith(suffix)})
    if not files:
        out["note"] = f"本机找不到会话记录 *{suffix}(跨机工位的会话记录在远端主机)"
        return out
    out["session_file"] = files[-1]
    last = None
    try:
        with open(files[-1], encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and _dig(entry, list(locator.get("role_path") or [])) == locator.get("assistant_role"):
                    last = entry
    except OSError as exc:
        out["note"] = f"会话记录读不出({type(exc).__name__}: {exc})"
        return out
    if last is None:
        out["note"] = "会话记录里无助手消息(模型未作答即退出)"
        return out
    stop = _dig(last, list(locator["stop_reason_path"]))
    error = str(_dig(last, list(locator.get("error_message_path") or [])) or "")
    tokens = [_dig(last, list(p)) for p in locator.get("input_token_paths") or []]
    out.update({"stop_reason": stop, "provider": _dig(last, list(locator.get("provider_path") or [])),
                "model": _dig(last, list(locator.get("model_path") or [])),
                "input_size": sum(int(x) for x in tokens if isinstance(x, int)) if any(isinstance(x, int) for x in tokens) else None,
                "error_excerpt": redact_progress(error.splitlines()[0] if error else "")[: int(early["excerpt_chars"])] or None})
    match = re.match(str(early["status_pattern"]), error) if error else None
    out["status"] = match.group("status") if match else None
    if stop == early["length_stop_reason"]:
        out["kind"] = "context_overflow"
    elif stop == early["error_stop_reason"]:
        out["kind"] = str((early["retry_statuses"] or {}).get(out["status"] or "") or "provider_error")
    else:
        out["kind"] = "unknown"
        out["note"] = f"最后一条助手消息 stopReason={stop}(非报错 / 非超窗口)"
    return out


def diagnosis_line(diag: dict[str, Any], early: dict[str, Any]) -> str:
    """诊断 → 一行人话(声明 early_exit.line; loop 结束原文与 loop status 同一渲染)。"""
    kinds = early["kinds"]
    values = {"kind_label": kinds.get(diag.get("kind")) or kinds["unknown"], "provider": diag.get("provider") or "?", "model": diag.get("model") or "?",
              "stop_reason": diag.get("stop_reason") or "?", "status_part": f", HTTP {diag['status']}" if diag.get("status") else "",
              "size_part": f", 本轮输入 {diag['input_size']} 词元" if diag.get("input_size") else "",
              "session_file": diag.get("session_file") or (diag.get("note") or "?"),
              "excerpt_part": f"; 错误首段: {diag['error_excerpt']}" if diag.get("error_excerpt") else ""}
    return str(early["line"]).format(**values)


# ---------------------------------------------------------------------------
# AIPOS-F231 件②: 协作式停线(声明 verbs.schema lybra_loop.stop; CLI = stop_cli; loop 侧 = loop_driver._stop_requested_end)
# ---------------------------------------------------------------------------

STOP_VERB = "lybra_loop_stop"
_STOP_KEYS = ("request_suffix", "default_timeout_seconds", "max_timeout_seconds", "kill_grace_seconds", "unknown", "end_reasons", "texts")
_STOP_TEXT_KEYS = ("requested", "stopped", "incomplete", "pending", "no_live_run", "dead_loop", "rechecked", "dry_run", "saved_header",
                   "cleaned_header", "residual_header", "resource_released", "resource_residual", "sim_residual", "process_residual",
                   "remote_unverified")


def stop_declaration(contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """verbs.schema lybra_loop.stop(fail-closed): 键齐 / 文案齐 / 时限为正 / end_reasons 三项为已声明结束原因。"""
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.schema_loader import SchemaLoadError

    contract = contract or load_loop_contract()
    decl = contract.get("stop") if isinstance(contract, dict) else None
    reasons = run_record_declaration(contract)["end_reasons"]
    where = "verbs.schema.json verbs.lybra_loop.stop"
    if not isinstance(decl, dict) or any(k not in decl for k in _STOP_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_STOP_KEYS)}")
    ends = decl["end_reasons"]
    if not isinstance(ends, dict) or set(ends) != {"owner", "advisor", "incomplete"} or any(v not in reasons for v in ends.values()):
        raise SchemaLoadError(f"{where}.end_reasons 须恰为 owner / advisor / incomplete 且各为 run_record.end_reasons 已声明者")
    if any(not isinstance(decl["texts"].get(k), str) or not decl["texts"][k] for k in _STOP_TEXT_KEYS):
        raise SchemaLoadError(f"{where}.texts 未声明齐 {list(_STOP_TEXT_KEYS)}")
    for key in ("default_timeout_seconds", "max_timeout_seconds", "kill_grace_seconds"):
        if not isinstance(decl[key], (int, float)) or isinstance(decl[key], bool) or decl[key] <= 0:
            raise SchemaLoadError(f"{where}.{key} 须为正数")
    return decl


def stop_request_path(record_path: Path) -> Path:
    """停线请求文件 = 运行记录同名 + request_suffix(*.log: 不入库, 运行记录 stop 段为真相)。"""
    record_path = Path(record_path)
    return record_path.with_name(record_path.stem + str(stop_declaration()["request_suffix"]))


def read_stop_request(record_path: Path) -> dict[str, Any]:
    """读停线请求; 读不出 / 形不合 = 仍是请求(停线优先), note 写明缘由, 时限取缺省。"""
    import json

    decl = stop_declaration()
    path = stop_request_path(record_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("非对象")
    except (OSError, ValueError) as exc:
        return {"requested_at": None, "requested_by": None, "reason": None, "owner_words": None, "relayed_by": None,
                "timeout": float(decl["default_timeout_seconds"]), "note": f"停线请求 {path} 读不出({type(exc).__name__}: {exc}), 按缺省时限处理"}
    try:
        timeout = float(data.get("timeout") or decl["default_timeout_seconds"])
    except (TypeError, ValueError):
        timeout = float(decl["default_timeout_seconds"])
    return {**data, "timeout": min(max(timeout, 1.0), float(decl["max_timeout_seconds"]))}


def _proc_ticks(fingerprint: Any) -> int | None:
    text = str(fingerprint or "")
    return int(text[len(FINGERPRINT_PREFIX_PROC):]) if text.startswith(FINGERPRINT_PREFIX_PROC) and text[len(FINGERPRINT_PREFIX_PROC):].isdigit() else None


def local_processes() -> list[dict[str, Any]] | None:
    """本机进程快照 [{pid, pgrp, ticks, uid, cwd}](Linux /proc; 无 /proc = None, 调用方按「无从核验」处理)。不读命令行与环境(只归属判据)。"""
    if not _proc_available():
        return None
    out: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            if fields[0] in ("Z", "X", "x"):
                continue
            uid = entry.stat().st_uid
        except (OSError, ValueError, IndexError):
            continue  # 进程已退出 / 不可读 = 不在快照(只动读得到且属本用户者)
        try:
            cwd = os.readlink(str(entry / "cwd"))
        except OSError:
            cwd = None
        out.append({"pid": int(entry.name), "pgrp": int(fields[2]), "ticks": int(fields[19]), "uid": uid, "cwd": cwd})
    return out


def card_validation_runs(governance_root: Path, cards: set[str]) -> list[dict[str, Any]]:
    """本卡(含其审计轮)验证根声明里未收口的 run(project.json validation_roots, 唯一读取口 validation_root.project_validation_roots):
    [{decl_id, task_id, host, local, run_id, run_dir, simulator, ssh_alias}]。读不出 = 向上(fail-closed)。"""
    from tools.aipos_cli import validation_root as vr

    wanted = {str(c).upper() for c in cards if c}
    hosts = vr.project_validation_hosts(governance_root)
    out: list[dict[str, Any]] = []
    for entry in vr.project_validation_roots(governance_root):
        if str(entry.get("task_id") or "").upper() not in wanted:
            continue
        for run in entry.get("runs") or []:
            if run.get("finished_at"):
                continue
            out.append({"decl_id": entry["id"], "task_id": entry["task_id"], "host": entry["host"], "local": vr.is_local_host(entry["host"]),
                        "run_id": run["run_id"], "run_dir": vr.run_directory(entry, run["run_id"]), "simulator": run.get("simulator"),
                        "ssh_alias": (hosts.get(str(entry["host"])) or {}).get("ssh_alias"), "entry": entry, "run": run})
    return out


def _within(path: str | None, base: str) -> bool:
    if not path:
        return False
    try:
        real, root = os.path.realpath(path), os.path.realpath(base)
    except OSError:
        return False
    return real == root or real.startswith(root.rstrip("/") + "/")


def _sim_residual(governance_root: Path, run: dict[str, Any], texts: dict[str, Any], task_id: str) -> dict[str, Any]:
    """本卡专用模拟器仍绑定于未收口 run = 未清理项(不自动删), 安全处置 = helper sim-delete + run-finish(命令唯一铸造口 validation_root)。"""
    from tools.aipos_cli import validation_root as vr

    entry, vrun, sim = run["entry"], run["run"], run["simulator"] or {}
    common = f"--run-dir {run['run_dir']} --expect-fingerprint {entry.get('host_fingerprint') or '<hfp1 指纹>'}"
    sim_delete = (vr.helper_pipe_command(governance_root, run["ssh_alias"], f"sim-delete {common}", entry, vrun) if run["ssh_alias"]
                  else vr.helper_local_command(governance_root, f"sim-delete {common}", entry, vrun))
    from tools.aipos_cli.workspace_config import lybra_command_prefix

    run_finish = (f"{lybra_command_prefix(governance_root)} project validation-root run-finish --id {entry['id']} --run-id {vrun['run_id']} "
                  "--sim-evidence <simulator.json 在治理根所在机的副本> --confirm --actor <顾问实例>")
    line = str(texts["sim_residual"]).format(name=sim.get("name"), udid=sim.get("udid"), host=run["host"], run_id=vrun["run_id"],
                                             sim_delete=sim_delete, run_finish=run_finish, task_id=task_id)
    return {"kind": "simulator", "host": run["host"], "run_dir": run["run_dir"], "udid": sim.get("udid"), "line": line}


def _remote_scan(remote: dict[str, Any], alias: str, run_dir: str) -> tuple[list[int] | None, str]:
    """经 ssh 列目标主机上命令行含本 run 目录的进程(harness_launch.remote_run_dir_pids 唯一实现); None = 无从核验(原因)。"""
    from tools.aipos_cli.harness_launch import remote_run_dir_pids

    return remote_run_dir_pids(remote, alias, run_dir)


def stop_candidates(governance_root: Path, meta: dict[str, Any], *, exclude_pids: set[int] | None = None) -> dict[str, Any]:
    """停线清理对象(只读; 只认核实属本卡者)。返回 {local: [{pid, ticks, why}], remote_groups: [{host, pgid, why}], remote_dirs:
    [{alias, host, run_dir, pids}], residual: [不自动清理的未清理项], notes: [...]}。
    归属: ①运行记录 launches 的进程组(本机: 组号 = 记录 pgid 且启动时刻不早于组长记录时刻、同一用户; 跨机: 未收尾拉起的 remote_pgid);
    ②本卡(运行里出现过的卡)验证根未收口 run 目录: 本机 = cwd 落在 run 目录下的同用户进程, 跨机 = ssh ps 命令行含 run 目录的进程;
    ③本卡专用模拟器(run 已绑定且未收口)= residual(给 sim-delete / run-finish)。"""
    from tools.aipos_cli.harness_launch import remote_declaration
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract

    decl = stop_declaration()
    texts = decl["texts"]
    exclude = {os.getpid(), *(exclude_pids or set())}
    local_host = socket.gethostname()
    out: dict[str, Any] = {"local": [], "remote_groups": [], "remote_dirs": [], "residual": [], "notes": []}
    procs = local_processes()
    me = os.getuid()
    task_id = str(meta.get("task_id") or "")
    cards = {task_id}
    seen: set[int] = set()

    def add_local(proc: dict[str, Any], why: str) -> None:
        if proc["pid"] in exclude or proc["pid"] in seen or proc["uid"] != me:
            return
        seen.add(proc["pid"])
        out["local"].append({"pid": proc["pid"], "ticks": proc["ticks"], "why": why})

    for launch in [x for x in (meta.get("launches") or []) if isinstance(x, dict)]:
        cards.add(str(launch.get("card") or ""))
        what = f"拉起 {launch.get('harness')} {launch.get('card')}"
        if launch.get("transport") == "remote":
            if launch.get("remote_pgid") and not launch.get("ended_at"):
                out["remote_groups"].append({"host": launch.get("host"), "pgid": int(launch["remote_pgid"]), "why": f"{what} 远端进程组 {launch['remote_pgid']}"})
            continue
        if launch.get("host") and str(launch.get("host")) != local_host:
            out["notes"].append(f"{what} 记于主机 {launch.get('host')}, 本机不清(请在该机执行)")
            continue
        pgid, leader = launch.get("pgid"), _proc_ticks(launch.get("process_fingerprint"))
        if not isinstance(pgid, int) or procs is None:
            if procs is None:
                out["residual"].append({"kind": "unverified", "line": f"本机无 /proc, {what} 进程组 {pgid} 无从核验: 手工核对后重跑 lybra loop stop 复核"})
            continue
        for proc in procs:
            if proc["pgrp"] == pgid and (leader is None or proc["ticks"] >= leader):
                add_local(proc, f"{what} 进程组 {pgid}(运行记录 launches)")
    for step in meta.get("steps") or []:
        if isinstance(step, dict) and step.get("card"):
            cards.add(str(step["card"]))
    for run in card_validation_runs(Path(governance_root), cards):
        if run["simulator"]:
            out["residual"].append(_sim_residual(Path(governance_root), run, texts, task_id))
        if run["local"]:
            if procs is None:
                out["residual"].append({"kind": "unverified", "line": str(texts["remote_unverified"]).format(host=run["host"], run_dir=run["run_dir"], why="本机无 /proc")})
                continue
            for proc in procs:
                if _within(proc["cwd"], run["run_dir"]):
                    add_local(proc, f"验证根 {run['decl_id']} run {run['run_id']} 目录下进程(cwd)")
            continue
        if not run["ssh_alias"]:
            out["residual"].append({"kind": "unverified", "host": run["host"], "run_dir": run["run_dir"],
                                    "line": str(texts["remote_unverified"]).format(host=run["host"], run_dir=run["run_dir"], why="项目未声明该主机 ssh 别名")})
            continue
        pids, why = _remote_scan(remote_declaration(launch_declaration(load_loop_contract())), str(run["ssh_alias"]), run["run_dir"])
        if pids is None:
            out["residual"].append({"kind": "unverified", "host": run["host"], "run_dir": run["run_dir"],
                                    "line": str(texts["remote_unverified"]).format(host=run["host"], run_dir=run["run_dir"], why=why)})
        elif pids:
            out["remote_dirs"].append({"alias": run["ssh_alias"], "host": run["host"], "run_dir": run["run_dir"], "pids": pids,
                                       "why": f"验证根 {run['decl_id']} run {run['run_id']} 目录下远端进程(命令行)"})
    return out


def _wait_gone(governance_root: Path, task_id: str, alive: Callable[[], bool], seconds: float) -> None:
    """有界等目标进程退出(经唯一等待原语 agent_watch_fs.run_fs_watch 的 stop_when; 禁 sleep 自旋)。"""
    import contextlib
    import io

    from tools.aipos_cli.agent_watch_fs import run_fs_watch
    from tools.aipos_cli.loop_driver import _watch_args

    if seconds <= 0 or not alive():
        return
    root = loop_runs_dir(Path(governance_root), task_id)
    root.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        run_fs_watch(_watch_args(root, [".lybra-stop-wait-never"], max_wait=seconds, interval=0.2), stop_when=lambda: not alive())


def stop_cleanup(governance_root: Path, meta: dict[str, Any], *, timeout: float, dry_run: bool = False,
                 exclude_pids: set[int] | None = None) -> dict[str, Any]:
    """按 stop_candidates 在 timeout 秒内有界清理: 本机 TERM → 等至多 kill_grace_seconds → KILL → 再等 → 仍在 = residual(process_residual);
    远端组 TERM → KILL(harness_launch.remote_signal)后核 gone; 远端 run 目录进程 TERM → KILL(remote_kill_pids)后重扫。dry_run = 只列不发信号。
    返回 {cleaned: [...], residual: [...], candidates}。"""
    import signal as _signal

    from tools.aipos_cli.harness_launch import remote_declaration, remote_kill_pids, remote_signal
    from tools.aipos_cli.loop_driver import launch_declaration, load_loop_contract

    decl = stop_declaration()
    texts = decl["texts"]
    task_id = str(meta.get("task_id") or "")
    cand = stop_candidates(Path(governance_root), meta, exclude_pids=exclude_pids)
    result: dict[str, Any] = {"cleaned": [], "residual": list(cand["residual"]), "candidates": cand, "notes": list(cand["notes"])}
    if dry_run:
        return result
    deadline = time.monotonic() + max(1.0, float(timeout))
    grace = float(decl["kill_grace_seconds"])
    targets = list(cand["local"])

    def alive_targets() -> list[dict[str, Any]]:
        return [x for x in targets if process_alive(x["pid"], f"{FINGERPRINT_PREFIX_PROC}{x['ticks']}")]

    for sig in (_signal.SIGTERM, _signal.SIGKILL):
        for item in alive_targets():
            try:
                os.kill(item["pid"], sig)
            except ProcessLookupError:
                pass  # 已退出 = 清干净
            except PermissionError as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
        _wait_gone(Path(governance_root), task_id, lambda: bool(alive_targets()), min(grace, deadline - time.monotonic()))
    left = alive_targets()
    for item in targets:
        if item in left:
            result["residual"].append({"kind": "process", "pid": item["pid"], "why": item["why"],
                                       "line": str(texts["process_residual"]).format(pid=item["pid"], where=item["why"], seconds=int(timeout), task_id=task_id)})
        else:
            result["cleaned"].append({"pid": item["pid"], "why": item["why"], "how": "SIGTERM/SIGKILL"})
    remote = None
    if cand["remote_groups"] or cand["remote_dirs"]:
        remote = remote_declaration(launch_declaration(load_loop_contract()))
    for group in cand["remote_groups"]:
        outcomes = [remote_signal(remote, str(group["host"]), group["pgid"], name) for name in ("TERM", "KILL")]
        probe = remote_signal(remote, str(group["host"]), group["pgid"], "0")
        if probe == "gone":
            result["cleaned"].append({"host": group["host"], "pgid": group["pgid"], "why": group["why"], "how": "/".join(outcomes)})
        else:
            result["residual"].append({"kind": "remote_group", "host": group["host"], "pgid": group["pgid"],
                                       "line": f"远端 {group['host']} 进程组 {group['pgid']}({group['why']})未确认退出({probe}): 在目标主机核对 `kill -s 0 -- -{group['pgid']}`, 清理后重跑 lybra loop stop --task-id {task_id} 复核"})
    for item in cand["remote_dirs"]:
        remote_kill_pids(remote, str(item["alias"]), item["pids"], "TERM")
        remote_kill_pids(remote, str(item["alias"]), item["pids"], "KILL")
        still, why = _remote_scan(remote, str(item["alias"]), item["run_dir"])
        if still is None or still:
            result["residual"].append({"kind": "remote_dir", "host": item["host"], "run_dir": item["run_dir"], "pids": still,
                                       "line": str(texts["remote_unverified"]).format(host=item["host"], run_dir=item["run_dir"], why=why or f"仍在 {still}")})
        else:
            result["cleaned"].append({"host": item["host"], "pids": item["pids"], "why": item["why"], "how": "TERM/KILL(ssh)"})
    return result


def stop_snapshot(launch: dict[str, Any], *, unknown: str, stderr_tail: list[str] | None = None) -> dict[str, Any]:
    """一次拉起的现场快照(停线先存): 命令(harness / 会话)、UTC 时刻、退出状态(仍在跑 / 未收尾 = unknown)、最近活动、stderr 末尾、指纹。"""
    from tools.aipos_cli.harness_launch import redact_progress

    act = launch.get("activity") or {}
    rc = launch.get("returncode")
    return {"card": launch.get("card"), "harness": launch.get("harness"), "session_id": launch.get("session_id"), "pid": launch.get("pid"),
            "pgid": launch.get("pgid"), "remote_pgid": launch.get("remote_pgid"), "host": launch.get("host"),
            "process_fingerprint": launch.get("process_fingerprint"), "started_at": launch.get("started_at"),
            "ended_at": launch.get("ended_at") or unknown, "outcome": launch.get("outcome") or unknown,
            "returncode": rc if isinstance(rc, int) else unknown, "last_activity_kind": act.get("last_activity_kind") or unknown,
            "last_activity_at": act.get("last_activity_at") or unknown,
            "stderr_tail": [redact_progress(x)[:200] for x in (stderr_tail or [])] or None}


def stop_report_lines(meta: dict[str, Any]) -> list[str]:
    """运行记录 stop 段 → 人读行(lybra loop stop 输出与 loop status 同一渲染)。"""
    decl = stop_declaration()
    texts = decl["texts"]
    stop = meta.get("stop") if isinstance(meta.get("stop"), dict) else None
    if not stop:
        return []
    out = [f"  停线请求 {stop.get('requested_at')} by {stop.get('requested_by') or '-'}" + (f"(Owner 原话「{stop['owner_words']}」, 经 {stop.get('relayed_by')})" if stop.get("owner_words") else "")
           + (f"; 原因: {stop['reason']}" if stop.get("reason") else "") + f"; outcome={stop.get('outcome') or '处理中'}"]
    saved = stop.get("saved") or {}
    out.append(f"  {texts['saved_header']} {saved.get('at') or decl['unknown']}; 日志 {saved.get('log_path') or decl['unknown']}")
    for item in saved.get("launches") or []:
        out.append(f"    - {item.get('harness')} {item.get('card')} 会话 {item.get('session_id') or '-'} pid {item.get('pid')} pgid {item.get('pgid')}"
                   f" 开始 {item.get('started_at')} 结束 {item.get('ended_at')} outcome {item.get('outcome')} returncode {item.get('returncode')}"
                   f" 最近活动 {item.get('last_activity_kind')} @ {item.get('last_activity_at')} 指纹 {item.get('process_fingerprint')}")
    if stop.get("cleaned"):
        out.append(f"  {texts['cleaned_header']}")
        out += [f"    - pid {c.get('pid') or c.get('pids') or c.get('pgid')} @ {c.get('host') or '本机'}: {c.get('why')}({c.get('how')})" for c in stop["cleaned"]]
    if stop.get("residual"):
        out.append(f"  {texts['residual_header']}")
        out += [f"    - {r.get('line')}" for r in stop["residual"]]
    lock = meta.get("resource_lock") if isinstance(meta.get("resource_lock"), dict) else None
    if lock:
        key = "resource_residual" if lock.get("state") == "residual" else ("resource_released" if lock.get("state") == "released" else None)
        if key:
            out.append("  " + str(texts[key]).format(resources=", ".join(lock.get("resources") or [])))
    return out


def finish_dead_run(path: Path, meta: dict[str, Any], *, request: dict[str, Any], saved: list[dict[str, Any]], cleanup: dict[str, Any],
                    reason: str, message: str, exit_code: int) -> dict[str, Any]:
    """loop 进程已不在(loop_dead)时由 lybra loop stop 写终态(同一渲染与原子写): stop 段 + 结束字段 + 独占资源 released / residual。"""
    from tools.aipos_cli.harness_launch import redact_progress

    now = iso_z()
    meta = {k: v for k, v in meta.items() if not str(k).startswith("_")}
    incomplete = bool(cleanup.get("residual"))
    meta["stop"] = {**{k: request.get(k) for k in ("requested_at", "requested_by", "reason", "owner_words", "relayed_by", "timeout")},
                    "saved": {"at": now, "current_step": meta.get("current_step"), "launches": saved, "log_path": meta.get("log_path"),
                              "note": "loop 进程已不在(loop_dead), 由 lybra loop stop 清理并写终态"},
                    "cleaned": list(cleanup.get("cleaned") or []), "residual": list(cleanup.get("residual") or []),
                    "outcome": "incomplete" if incomplete else "stopped", "finished_at": now}
    lock = meta.get("resource_lock")
    if isinstance(lock, dict):
        meta["resource_lock"] = {**lock, "state": "residual" if incomplete else "released", ("residual_at" if incomplete else "released_at"): now}
    meta.update({"status": "ended", "ended_at": now, "updated_at": now, "outcome": "stopped", "exit_code": int(exit_code), "end_reason": reason,
                 "end_message": redact_progress(message.splitlines()[0] if message else "")[:240] or None, "end_codes": None, "current_step": None})
    _write_record(Path(path), meta)
    log = meta.get("log_path")
    if log and Path(str(log)).exists():
        with Path(str(log)).open("a", encoding="utf-8") as handle:
            handle.write(f"{now} [end] outcome=stopped exit={exit_code} reason={reason}(loop_dead, 由 lybra loop stop 收口)\n")
    return meta


def recheck_residual(path: Path, meta: dict[str, Any], cleanup: dict[str, Any]) -> dict[str, Any]:
    """stop_incomplete 运行的复核: 无残留 = resource_lock.state released(released_at)+ stop.rechecked_at / outcome stopped; 有 = 更新 residual 列表。"""
    now = iso_z()
    meta = {k: v for k, v in meta.items() if not str(k).startswith("_")}
    stop = dict(meta.get("stop") or {})
    stop.update({"rechecked_at": now, "residual": list(cleanup.get("residual") or []),
                 "cleaned": list(stop.get("cleaned") or []) + list(cleanup.get("cleaned") or [])})
    if not cleanup.get("residual"):
        stop["outcome"] = "stopped"
        lock = meta.get("resource_lock")
        if isinstance(lock, dict):
            meta["resource_lock"] = {**lock, "state": "released", "released_at": now}
    meta["stop"] = stop
    meta["updated_at"] = now
    _write_record(Path(path), meta)
    return meta


def _latest_meta(governance_root: Path, task_id: str, decl: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any] | None:
    """本卡最近一次运行(单实例锁拒掉的启动不算, 与 loop status 同一口径)。"""
    refused = single_instance_declaration(contract)["end_reason"]
    metas = [m for m in (read_run(p, decl) for p in _run_files(loop_runs_dir(governance_root, task_id)))
             if not (m.get("status") == "ended" and m.get("end_reason") == refused)]
    metas.sort(key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
    return metas[-1] if metas else None


def stop_run(governance_root: Path, task_id: str, *, requested_by: str, reason: str | None = None, owner_words: str | None = None,
             relayed_by: str | None = None, timeout: float | None = None, dry_run: bool = False,
             contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F231 件② `lybra loop stop` 的唯一实现(CLI = stop_cli 薄壳)。→ {outcome: stopped | incomplete | pending | no_live_run | dry_run,
    run_id, record, lines(人读), meta}。在跑(running / stalled / launch_dead)= 写停线请求后经唯一等待原语 run_fs_watch 有界等该运行写结束
    (≤ timeout); loop_dead = 本命令自做同一清理(stop_cleanup)并写终态(finish_dead_run); 最近运行以 stop_incomplete 结束(资源 residual)= 复核
    (recheck_residual); unprobeable = 拒(在治理根所在机执行)。"""
    import contextlib
    import io
    import json

    from tools.aipos_cli.agent_watch_fs import run_fs_watch
    from tools.aipos_cli.loop_driver import _watch_args, exit_code_for, load_loop_contract

    contract = contract or load_loop_contract()
    decl = run_record_declaration(contract)
    sdecl = stop_declaration(contract)
    texts = sdecl["texts"]
    si = single_instance_declaration(contract)
    timeout = float(timeout if timeout is not None else sdecl["default_timeout_seconds"])
    if not 0 < timeout <= float(sdecl["max_timeout_seconds"]):
        raise StatusUsageError(f"--timeout 须在 (0, {sdecl['max_timeout_seconds']}] 秒")
    if owner_words and not str(relayed_by or "").strip():
        raise StatusUsageError("带 --owner-words(Owner 原话)须同时给 --relayed-by <转达的顾问实例>")
    meta = _latest_meta(Path(governance_root), task_id, decl, contract)
    if meta is None:
        return {"outcome": "no_live_run", "lines": [str(texts["no_live_run"]).format(task_id=task_id, latest="(无运行记录)")], "meta": None}
    record = Path(str(meta["_path"]))
    view = judge_run(meta, decl)
    unknown = str(sdecl["unknown"])
    request = {"requested_at": iso_z(), "requested_by": requested_by, "reason": reason or None, "owner_words": owner_words or None,
               "relayed_by": relayed_by or None, "timeout": timeout}
    base = {"run_id": meta.get("run_id"), "record": str(record)}
    if view["state"] == "ended":
        lock = meta.get("resource_lock") if isinstance(meta.get("resource_lock"), dict) else {}
        if lock.get("state") != "residual":
            latest = f"run {meta.get('run_id')} 已结束 {meta.get('ended_at')} reason={meta.get('end_reason')}"
            return {**base, "outcome": "no_live_run", "lines": [str(texts["no_live_run"]).format(task_id=task_id, latest=latest)], "meta": meta}
        cleanup = stop_cleanup(Path(governance_root), meta, timeout=timeout, dry_run=dry_run)
        if dry_run:
            return {**base, "outcome": "dry_run", "meta": meta, "cleanup": cleanup,
                    "lines": [str(texts["dry_run"]).format(run_id=meta.get("run_id"))] + _cleanup_lines(cleanup, texts)}
        meta = recheck_residual(record, meta, cleanup)
        outcome = "incomplete" if cleanup["residual"] else "stopped"
        head = texts["incomplete"] if cleanup["residual"] else texts["rechecked"]
        return {**base, "outcome": outcome, "meta": meta, "lines": [str(head)] + stop_report_lines(meta)}
    if view["state"] == "unprobeable":
        return {**base, "outcome": "unprobeable", "meta": meta, "lines": [f"运行记录写于主机 {meta.get('host')}, 本机无从探活与清理: 请在治理根所在机执行 lybra loop stop"]}
    if dry_run:
        cleanup = stop_cleanup(Path(governance_root), meta, timeout=timeout, dry_run=True, exclude_pids={int(meta.get("pid") or 0)})
        return {**base, "outcome": "dry_run", "meta": meta, "cleanup": cleanup,
                "lines": [str(texts["dry_run"]).format(run_id=meta.get("run_id")), f"  运行状态 {view['state']}"] + _cleanup_lines(cleanup, texts)}
    if view["state"] == "loop_dead":
        saved = [stop_snapshot(x, unknown=unknown) for x in meta.get("launches") or [] if isinstance(x, dict)]
        cleanup = stop_cleanup(Path(governance_root), meta, timeout=timeout)
        incomplete = bool(cleanup["residual"])
        end = str(sdecl["end_reasons"]["incomplete" if incomplete else ("owner" if owner_words else "advisor")])
        msg = str(texts["dead_loop"]) + "; " + str(texts["incomplete" if incomplete else "stopped"]).replace("{reason}", end)
        meta = finish_dead_run(record, meta, request=request, saved=saved, cleanup=cleanup, reason=end, message=msg,
                               exit_code=exit_code_for(contract, "stop_requested"))
        return {**base, "outcome": "incomplete" if incomplete else "stopped", "meta": meta, "lines": [msg] + stop_report_lines(meta)}
    path = stop_request_path(record)
    if not path.exists():
        tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(request, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        exclude_run_logs(record.parent)
    lines = [str(texts["requested"]).format(request=path, run_id=meta.get("run_id"), pid=meta.get("pid"), host=meta.get("host"), timeout=f"{timeout:g}")]

    def _ended(_matched: list[str]) -> bool:
        try:
            return read_run(record, decl).get("status") == "ended"
        except LoopRunRecordError:
            return False

    def _loop_gone() -> bool:
        return not process_alive(meta.get("pid"), meta.get("process_fingerprint"))

    with contextlib.redirect_stdout(io.StringIO()):
        run_fs_watch(_watch_args(record.parent, [record.name], max_wait=timeout, interval=0.5), expect_ready=_ended, stop_when=_loop_gone)
    final = read_run(record, decl)
    if final.get("status") != "ended":
        if _loop_gone():  # 等待中 loop 进程没了(被杀 / 崩溃)= 本命令接手清理
            return stop_run(governance_root, task_id, requested_by=requested_by, reason=reason, owner_words=owner_words,
                            relayed_by=relayed_by, timeout=timeout, contract=contract)
        lock = final.get("resource_lock") if isinstance(final.get("resource_lock"), dict) else None
        pending = [str(texts["pending"]).format(timeout=f"{timeout:g}")]
        pending.append(f"  {texts['saved_header']} 停线请求 {path}(loop 尚未存现场; 运行记录 {record})")
        pending.append(f"  {texts['residual_header']}")
        pending += [f"    - 拉起 {x.get('harness')} {x.get('card')} pid {x.get('pid')} pgid {x.get('pgid')}(outcome {x.get('outcome') or unknown})"
                    for x in final.get("launches") or [] if isinstance(x, dict) and not x.get("ended_at")] or [f"    - loop pid {final.get('pid')} 仍在推进(当前步 {_step_label(final.get('current_step'))})"]
        if lock:
            pending.append("  " + str(texts["resource_residual"]).format(resources=", ".join(lock.get("resources") or [])))
        return {**base, "outcome": "pending", "meta": final, "lines": lines + pending}
    stop = final.get("stop") if isinstance(final.get("stop"), dict) else {}
    outcome = "incomplete" if stop.get("outcome") == "incomplete" else ("stopped" if stop else "ended_otherwise")
    head = {"stopped": texts["stopped"], "incomplete": texts["incomplete"]}.get(outcome, f"运行在处理停线前已以 {final.get('end_reason')} 结束")
    return {**base, "outcome": "stopped" if outcome == "ended_otherwise" else outcome, "meta": final,
            "lines": lines + [str(head).replace("{reason}", str(final.get("end_reason")))] + stop_report_lines(final)}


def _cleanup_lines(cleanup: dict[str, Any], texts: dict[str, Any]) -> list[str]:
    cand = cleanup.get("candidates") or {}
    out = [f"  将清理(本机): pid {c['pid']} — {c['why']}" for c in cand.get("local") or []]
    out += [f"  将清理(远端组): {g['host']} pgid {g['pgid']} — {g['why']}" for g in cand.get("remote_groups") or []]
    out += [f"  将清理(远端 run 目录): {d['host']} pid {d['pids']} — {d['why']}" for d in cand.get("remote_dirs") or []]
    if cleanup.get("residual"):
        out.append(f"  {texts['residual_header']}")
        out += [f"    - {r.get('line')}" for r in cleanup["residual"]]
    out += [f"  注: {n}" for n in cleanup.get("notes") or []]
    return out or ["  (无本卡进程需清理)"]


def stop_cli(args: Any) -> int:
    """`lybra loop stop --task-id <卡> [--reason] [--owner-words --relayed-by] [--timeout] [--dry-run] [--json]`(声明 verbs.schema lybra_loop_stop)。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    task_id = getattr(args, "task_id", None)
    if not task_id:
        print("lybra loop stop: error: the following arguments are required: --task-id", file=sys.stderr)
        return declared_exit_code(STOP_VERB, "usage")
    try:
        governance_root = Path(_find_repo_root_for_args(args))
        report = stop_run(governance_root, str(task_id), requested_by=str(getattr(args, "actor", None) or getattr(args, "relayed_by", None) or "advisor"),
                          reason=getattr(args, "reason", None), owner_words=getattr(args, "owner_words", None),
                          relayed_by=getattr(args, "relayed_by", None), timeout=getattr(args, "stop_timeout", None),
                          dry_run=bool(getattr(args, "dry_run", False)))
    except StatusUsageError as exc:
        print(f"lybra loop stop: {exc}", file=sys.stderr)
        return declared_exit_code(STOP_VERB, "usage")
    except (FileNotFoundError, LoopRunRecordError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra loop stop: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(STOP_VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps({k: v for k, v in report.items() if k != "meta"} | {"stop": (report.get("meta") or {}).get("stop"),
                          "resource_lock": (report.get("meta") or {}).get("resource_lock")}, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n".join(report["lines"]))
    code = {"stopped": "stopped", "dry_run": "stopped", "no_live_run": "no_live_run", "incomplete": "incomplete", "pending": "incomplete",
            "unprobeable": "unreadable"}[report["outcome"]]
    return declared_exit_code(STOP_VERB, code)


def loop_status(governance_root: Path, task_id: str | None = None, *, now: datetime | None = None,
                contract: dict[str, Any] | None = None, lane: str | list[str] | None = None,
                include_frozen: bool = False) -> dict[str, Any]:
    """--task-id 给出 = 该卡最近一次运行(含已结束); 缺省 = 本项目全部未结束的运行(loop_dead 也列出: 记录未结束而进程已不在)。
    读不出的记录 = LoopRunRecordError(fail-closed, 点名文件)。
    AIPOS-F133 件②: 每个运行带 lane(_run_lane 反查卡面)。AIPOS-F141: 运行行经四视图唯一可见卡入口 machine_zone.visible_cards
    (冻结卡的运行缺省不列 = F122 唯一判定; lane 给出 = 只列所选, 未解析 lane 的运行单独成组); --task-id = 显式点名, 照列(冻结标 frozen)。
    AIPOS-F142 件①: 一次 status 全程一个只读作用域(task_loader.task_card_lookup_scope): 运行行反查卡面、结案取下一张的扫描共用一趟
    队列索引, 冻结清单与信封计数各只算一次; --task-id 只读该卡运行记录; 已结束运行 judge_run 不探活。每次调用(含 --wait 每轮)新建作用域。"""
    from tools.aipos_cli.task_loader import task_card_lookup_scope

    with task_card_lookup_scope(Path(governance_root)):
        report = _loop_status(Path(governance_root), task_id, now=now, contract=contract, lane=lane, include_frozen=include_frozen)
        if task_id:  # AIPOS-F162 件②: 本卡补充审计链路(唯一实现 audit_derivation.supplementary_audit_chain; 同一作用域, 不另扫队列)
            from tools.aipos_cli.audit_derivation import supplementary_audit_chain
            from tools.aipos_cli.owner_inbox import auto_release_records, revoked_auto_release_ids

            report["supplementary_audit"] = supplementary_audit_chain(Path(governance_root), task_id)
            # AIPOS-F225 件②: 本卡第二档自动放行记录(读口唯一 owner_inbox.auto_release_records; 已撤回标 revoked)
            revoked = revoked_auto_release_ids(Path(governance_root), task_id)
            report["auto_release_records"] = [{k: r.get(k) for k in ("auto_release_id", "kind", "released_at", "basis", "round", "verdict_ref", "_path")}
                                       | {"revoked": str(r.get("auto_release_id") or "") in revoked}
                                       for r in auto_release_records(Path(governance_root), task_id)]
        return report


def _loop_status(governance_root: Path, task_id: str | None, *, now: datetime | None, contract: dict[str, Any] | None,
                 lane: str | list[str] | None, include_frozen: bool) -> dict[str, Any]:
    from tools.aipos_cli.machine_zone import visible_cards

    from tools.aipos_cli.loop_driver import load_loop_contract

    decl = run_record_declaration(contract or load_loop_contract())
    rules = next_action_declaration(decl)
    root = loop_runs_root(Path(governance_root))
    history: list[Any] = []
    code_history: list[Any] = []
    if task_id:
        files = _run_files(loop_runs_dir(governance_root, task_id))
        # AIPOS-F171B 件①: 单实例锁拒掉的启动(从未推进)不算「最近一次运行」, 也不进连败计数
        refused = single_instance_declaration(contract or load_loop_contract())["end_reason"]
        metas = [m for m in (read_run(p, decl) for p in files)
                 if not (m.get("status") == "ended" and m.get("end_reason") == refused)]
        metas.sort(key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
        picked = metas[-1:]
        total = len(metas)
        history = [m.get("end_reason") if m.get("status") == "ended" else None for m in metas[:-1]]
        code_history = [[str(c) for c in (m.get("end_codes") or [])] if m.get("status") == "ended" else None for m in metas[:-1]]
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
                                          earlier_end_reasons=history if task_id else None,
                                          earlier_end_codes=code_history if task_id else None)
        # AIPOS-F168 件②: 该运行对应的 Owner 收件箱事项已裁决 = 下一动作改为 advisor_fix(owner_ruled), detail 带裁决原文, hint 带续跑命令
        from tools.aipos_cli.owner_inbox import apply_card_pause, apply_owner_ruling

        view["next_action"] = apply_owner_ruling(Path(governance_root), view, view["next_action"])
        # AIPOS-F231 件①: 本卡已被 Owner 暂停 = 下一动作「已暂停」(owner_needed paused, 给恢复命令, 不给重跑 loop); 已恢复 = 续跑(唯一实现 owner_inbox.apply_card_pause)
        view["next_action"] = apply_card_pause(Path(governance_root), view, view["next_action"])
        # AIPOS-F225: 仍等 Owner 的按档归类(第一档留 owner_needed 带事由; 第二档自动路径; 第三档归顾问), 唯一实现 owner_inbox.apply_owner_tier
        from tools.aipos_cli.owner_inbox import apply_owner_tier

        view["next_action"] = apply_owner_tier(Path(governance_root), view, view["next_action"])
        if task_id and view.get("state") == "ended":
            # AIPOS-F230 件①: 本卡此刻处非通过状态 = 附全部合法出口(唯一渲染 next_resolver.legal_exits, 与 lybra next 同源)
            from tools.aipos_cli.next_resolver import legal_exits_for_card

            exits = legal_exits_for_card(Path(governance_root), task_id)
            if exits:
                hint = str(view["next_action"].get("hint") or "")
                view["next_action"] = {**view["next_action"], "legal_exits": exits,
                                       "hint": f"{hint}\n{exits['text']}" if hint else str(exits["text"])}
        if meta.get("lane_overlaps"):  # AIPOS-F188 件③: 起跑认领前判出的车道重叠(运行记录原样)
            view["lane_overlaps"] = list(meta["lane_overlaps"])
        warning = envelope_warning_view(Path(governance_root), view, now=now)  # AIPOS-F166 件②: 运行所用信封的用量预警
        if warning:
            view["envelope_warning"] = warning
        views.append(view)
    view = visible_cards(Path(governance_root), ((v, None) for v in views), lane=lane, include_frozen=include_frozen or bool(task_id))
    views = view["rows"]
    report = {"loop_runs_root": str(root), "task_id": task_id, "lane_filter": lane, "runs": views, "runs_on_record": total,
              "stall_after_seconds": int(decl["stall_after_seconds"]), "stall_after_tool_seconds": int(decl["stall_after_tool_seconds"]),
              "frozen_hidden": view["frozen_hidden"], "unresolved_lane": view["unresolved_lane"], "frozen_error": view["frozen_error"]}
    if task_id:
        report["next_action"] = views[-1]["next_action"] if views else None
    # AIPOS-F166 件③: 合并后回归未完成(唯一判定 post_merge_regression.regression_states; --task-id = 本卡, 缺省 = 全项目)
    from tools.aipos_cli.post_merge_regression import unfinished_regressions

    report["post_merge_regression_unfinished"] = unfinished_regressions(Path(governance_root), task_id=task_id)
    return report


# ---------------------------------------------------------------------------
# AIPOS-F136 件①: 顾问下一动作(唯一判定)+ 有界等待(经 run_fs_watch)
# ---------------------------------------------------------------------------

NEXT_ACTIONS = ("continue_wait", "owner_needed", "card_done_take_next", "investigate", "advisor_fix")
_NEXT_ACTION_KEYS = ("actions", "by_state", "by_end_reason", "owner_reasons", "consecutive_failures_owner_needed",
                     "take_next_hint", "take_next_none_hint", "wait_hint", "investigate_hint", "routine_rejections", "envelope_renew")
_ROUTINE_KEYS = ("repeat_owner_needed", "placeholders", "resume_hint", "codes")


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
    escalations = [k for k in ("consecutive_failures", "routine_fix_exhausted") if k not in decl["owner_reasons"]]
    if gaps or bad or owner_gaps or escalations:
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action 判据表不全: 未覆盖 {gaps}; 非法动作 {bad}; "
                              f"owner_needed 缺事由 {owner_gaps + escalations}"
                              f"(须覆盖 lybra_loop.run_record.states / end_reasons)")
    _check_routine_rejections(decl["routine_rejections"])
    exempt = decl.get("consecutive_failures_exempt")  # AIPOS-F231 件⑥: 可选; 在则须为已声明结束原因的列表
    if exempt is not None and (not isinstance(exempt, list) or set(exempt) - reasons):
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.consecutive_failures_exempt 须为已声明结束原因的列表")
    review = decl.get("advisor_review_close")  # AIPOS-F207 件③: 可选; 在则结束原因须已声明、文案齐
    if review is not None and (not isinstance(review, dict) or not isinstance(review.get("end_reasons"), list)
                               or set(review["end_reasons"]) - reasons or not isinstance(review.get("verbs"), list) or not review["verbs"]
                               or any(not isinstance(review.get(k), str) or not review[k] for k in ("detail", "hint"))):
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.advisor_review_close 须含已声明结束原因的 end_reasons、非空 verbs 与 detail / hint")
    # AIPOS-F166 件②: 信封续签映射——所列结束原因须已声明、by_end_reason 判 owner_needed, reason 须有事由
    renew = decl["envelope_renew"]
    renew_ends = renew.get("end_reasons") if isinstance(renew, dict) else None
    if (not isinstance(renew_ends, list) or not renew_ends or set(renew_ends) - reasons
            or any(decl["by_end_reason"].get(r) != "owner_needed" for r in renew_ends)
            or not isinstance(renew.get("reason"), str) or renew["reason"] not in decl["owner_reasons"]):
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.envelope_renew: end_reasons 须为已声明且 by_end_reason=owner_needed 的结束原因, "
                              "reason 须在 owner_reasons")
    # AIPOS-F152 件①: by_end_reason 判为 advisor_fix 的结束原因须在 routine_rejections.end_reasons 有修法(缺 = 拒, 不猜)
    routine_reasons = decl["routine_rejections"].get("end_reasons") or {}
    if not isinstance(routine_reasons, dict) or set(routine_reasons) - reasons:
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.routine_rejections.end_reasons 须为对象且键 ⊆ lybra_loop.run_record.end_reasons, "
                              f"多出 {sorted(set(routine_reasons) - reasons) if isinstance(routine_reasons, dict) else routine_reasons!r}")
    # AIPOS-F156 件①: 按执行方式覆盖(by_execution_mode)同受上述约束——覆盖到 advisor_fix 的结束原因也须有修法
    overrides = _check_execution_modes(decl, reasons)
    for reason in sorted({r for r, a in decl["by_end_reason"].items() if a == "advisor_fix"} | overrides):
        fix = (routine_reasons.get(reason) or {}).get("fix")
        if not isinstance(fix, list) or not fix or any(not isinstance(x, str) or not x.strip() for x in fix):
            raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.by_end_reason.{reason} = advisor_fix, 但 routine_rejections.end_reasons."
                                  f"{reason}.fix 未声明为非空字符串的非空列表")
    # AIPOS-F204 件③: 交回报告头复核(可选)——所列结束原因须已声明; 复核成立时视同的结束原因(launch.report_fix.executor.end_reason)
    # 须 by_end_reason = advisor_fix(上一条已保证其有修法)
    probe = decl["routine_rejections"].get("return_header_probe")
    if probe is not None:
        ends = probe.get("end_reasons") if isinstance(probe, dict) else None
        if not isinstance(ends, list) or not ends or set(ends) - reasons:
            raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.routine_rejections.return_header_probe.end_reasons 须为已声明结束原因的非空列表")
        from tools.aipos_cli.loop_driver import load_loop_contract, report_fix_declaration

        contract = load_loop_contract()
        # AIPOS-F232 件②: frontmatter_unreadable 复核可视同审计卡的修报告头结束原因(审计 / 执行两份声明都核)
        for fix_decl in (report_fix_declaration(contract), report_fix_declaration(contract, "executor")):
            as_reason = str(fix_decl["end_reason"])
            if decl["by_end_reason"].get(as_reason) != "advisor_fix":
                raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.by_end_reason.{as_reason} 须为 advisor_fix(return_header_probe 视同的结束原因)")
    # AIPOS-F225 件①: Owner 介入分级映射守卫(每个可判 owner_needed 的事由须已归档, 唯一实现 owner_inbox._check_tiers, 经 inbox_declaration 加载)
    from tools.aipos_cli.owner_inbox import inbox_declaration

    inbox_declaration()
    return decl


def envelope_warning_view(governance_root: Path, view: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    """AIPOS-F166 件②: 运行所用信封的预警(判定唯一 autonomy_policy.envelope_warning; 用量真相 envelope_usage 现读门侧计数)。
    达阈值 = {policy_id, state, why, line(声明 envelope.renew.messages.warn 渲染, 含续签命令)}; 否 / 无信封 / 信封缺 = None。
    AIPOS-F172(gap #160): 状态取信封状态单一推导 autonomy_policy.envelope_status(看板信封页 / 收件箱同读)——已被同驱动方更新信封接替 = 不预警。
    AIPOS-F232 件③: 运行用的是项目授权(或已迁入项目授权的旧信封)= 该授权的告知(autonomy_policy.authorization_warnings: 已用 / 到期, 含扩大
    授权命令; 收件箱 / brief 同读), {policy_id = 授权 ID, state = authorization_notice, why, line}; 无告知 = None。"""
    from tools.aipos_cli.autonomy_policy import (authorization_superseded_policies, authorization_warnings, envelope_renew_declaration,
                                                 envelope_status, is_authorization_id, renew_plan)

    pid = str(view.get("envelope") or "").strip()
    if not pid:
        return None
    aid = pid if is_authorization_id(governance_root, pid) else authorization_superseded_policies(governance_root).get(pid)
    if aid:
        notices = authorization_warnings(governance_root, now=now, authorization_id=aid)
        if not notices:
            return None
        return {"policy_id": aid, "state": "authorization_notice", "why": notices[0]["why"], "next_policy_id": aid,
                "line": notices[0]["line"]}
    row = envelope_status(governance_root, pid, now=now)
    why = (row or {}).get("why")
    if not row or row["superseded_by"] or not why:
        return None
    usage = row["usage"]
    driver = str(view.get("driver") or "").strip()
    plan = renew_plan(governance_root, usage, identities={driver, "advisor"} - {""}, task_id=str(view.get("task_id") or ""), now=now)
    line = envelope_renew_declaration()["messages"]["warn"].format(policy_id=pid, why=why, command=plan["renew_command"])
    return {"policy_id": pid, "state": usage.get("state"), "why": why, "next_policy_id": plan["next_policy_id"], "line": line}


def _check_execution_modes(decl: dict[str, Any], reasons: set[str]) -> set[str]:
    """AIPOS-F156 件①: next_action.by_execution_mode 形校验(fail-closed): modes 键 ⊆ config.schema project_json.execution.default_mode
    值域; 每个 mode 的 by_end_reason 键 ⊆ 已声明结束原因、值 ∈ 下一动作; kickoff_detail 为含 {hint} 的非空串。返回覆盖到 advisor_fix 的结束原因。
    缺段 = 不覆盖(空集合, 行为不变)。"""
    from tools.aipos_cli.workspace_config import execution_declaration
    from tools.schema_loader import SchemaLoadError

    where = f"verbs.{STATUS_VERB}.next_action.by_execution_mode"
    spec = decl.get("by_execution_mode")
    if spec is None:
        return set()
    modes = spec.get("modes") if isinstance(spec, dict) else None
    allowed = {str(m) for m in execution_declaration()["schema"]["default_mode"]["enum"]}
    if not isinstance(modes, dict) or set(modes) - allowed:
        raise SchemaLoadError(f"{where}.modes 须为对象且键 ⊆ config.schema project_json.execution.default_mode 值域 {sorted(allowed)}")
    fixes: set[str] = set()
    for mode, rule in modes.items():
        table = rule.get("by_end_reason") if isinstance(rule, dict) else None
        kickoff = rule.get("kickoff_detail") if isinstance(rule, dict) else None
        if (not isinstance(table, dict) or set(table) - reasons or set(table.values()) - set(NEXT_ACTIONS)
                or not isinstance(kickoff, str) or "{hint}" not in kickoff):
            raise SchemaLoadError(f"{where}.modes.{mode}: by_end_reason 须为 已声明结束原因 → 下一动作, kickoff_detail 须为含 {{hint}} 的串")
        fixes |= {r for r, a in table.items() if a == "advisor_fix"}
    return fixes


def card_wait_execution_mode(governance_root: Path, task_id: str, fm: dict[str, Any]) -> str | None:
    """AIPOS-F185 件①: 等交回所按的执行方式(唯一判定; loop status 下一动作 run_execution_mode 与 loop 等待上限 loop_driver.loop_wait_limit
    同读)。= workspace_config.card_execution_mode(只读 F143 声明); 非子 agent 而等待目标实例是无工位的子 agent 执行者(接入登记
    governance_root_mode, loop_driver.wait_instance_is_subagent)= 子 agent。未声明 execution = None。声明不合 = ExecutionDeclarationError 上抛。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.loop_driver import wait_instance_is_subagent
    from tools.aipos_cli.workspace_config import EXECUTION_MODE_SUBAGENT, card_execution_mode, project_execution

    mode = card_execution_mode(project_execution(Path(governance_root)), fm, audit_card=is_audit_card(task_id, fm))
    # AIPOS-F157 件③(gap #125): 等待目标实例是无工位的子 agent 执行者 = 子 agent 口径——与 loop_driver.plan_launch 开工提示同一判定
    if mode != EXECUTION_MODE_SUBAGENT and wait_instance_is_subagent(Path(governance_root), task_id, fm):
        return EXECUTION_MODE_SUBAGENT
    return mode


def run_execution_mode(governance_root: Path, view: dict[str, Any]) -> tuple[str | None, str]:
    """AIPOS-F156 件①: 运行视图的执行方式 —— 最后一次等待的卡即本卡(执行体等待)时, 经 workspace_config.card_execution_mode
    (只读 F143 声明唯一读取口 project_execution)判出; 等待别的卡(审计 / 外部 FINALIZE / 同仓先合入)、未声明 execution = (None, "")。
    判不出(卡找不到 / 读不出 / 声明不合)= (None, 原因)——调用方按原口径(owner_needed), 原因进 detail, 不猜。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card
    from tools.aipos_cli.workspace_config import ExecutionDeclarationError

    task_id = str(view.get("task_id") or "")
    if not task_id or view.get("wait_card") != task_id:
        return None, ""
    try:
        path, _state = find_task_card(Path(governance_root), task_id)
        if path is None:
            return None, f"队列中找不到卡 {task_id}, 执行方式判不出"
        fm, _body = require_frontmatter(path)
        # 未声明 execution 时等交回超时也不再出「请 Owner 在工位敲 /go」(子 agent 执行者实例同判, 见 card_wait_execution_mode)
        return card_wait_execution_mode(Path(governance_root), task_id, fm), ""
    except (AmbiguousTaskCard, FrontmatterReadError, ExecutionDeclarationError, OSError, ValueError) as exc:
        return None, f"执行方式判不出({type(exc).__name__}: {exc})"


def _execution_rule(rules: dict[str, Any], mode: str | None) -> dict[str, Any]:
    """by_execution_mode.modes.<执行方式>(无 / 未声明 = {})。"""
    modes = (rules.get("by_execution_mode") or {}).get("modes") or {}
    return (modes.get(mode) or {}) if mode else {}


def _check_routine_rejections(routine: Any) -> None:
    """AIPOS-F150 件②: 例行门拒声明形校验(fail-closed): 键齐; repeat_owner_needed 为 ≥1 整数; 每码 fix 为非空字符串的非空列表;
    码形 = 门侧错误码形(大写 + 下划线)。"""
    from tools.schema_loader import SchemaLoadError

    where = f"verbs.{STATUS_VERB}.next_action.routine_rejections"
    if not isinstance(routine, dict) or any(k not in routine for k in _ROUTINE_KEYS):
        raise SchemaLoadError(f"{where} 未声明齐 {list(_ROUTINE_KEYS)}")
    n = routine["repeat_owner_needed"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise SchemaLoadError(f"{where}.repeat_owner_needed 须为 ≥1 整数, 得到 {n!r}")
    codes = routine["codes"]
    if not isinstance(codes, dict):
        raise SchemaLoadError(f"{where}.codes 须为对象(码 → {{why_routine, fix}})")
    for code, spec in codes.items():
        fix = spec.get("fix") if isinstance(spec, dict) else None
        if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", str(code)) or not isinstance(fix, list) or not fix \
                or any(not isinstance(s, str) or not s.strip() for s in fix):
            raise SchemaLoadError(f"{where}.codes.{code}: 码须为门侧错误码形且 fix 为非空字符串的非空列表")
    probe = routine.get("landing_probe")  # AIPOS-F195 件③: 可选; 在则 end_reasons 为非空列表(码读 transitions N6.landing.slot_code_files)
    if probe is not None and (not isinstance(probe, dict) or not isinstance(probe.get("end_reasons"), list) or not probe["end_reasons"]):
        raise SchemaLoadError(f"{where}.landing_probe 须含非空 end_reasons 列表")


def next_action(view: dict[str, Any], rules: dict[str, Any], *, governance_root: Path,
                earlier_end_reasons: list[Any] | None = None,
                earlier_end_codes: list[Any] | None = None) -> dict[str, Any]:
    """一个运行视图(judge_run)→ 顾问下一动作 {action, reason, detail, hint}(唯一判定, 判据表 verbs.schema next_action)。

    earlier_end_reasons = 本卡更早各次运行的结束原因(旧→新; 未结束 = None), 用于连败判定: 本次与其前连续
    consecutive_failures_owner_needed 次都以 investigate 类原因结束 = owner_needed(consecutive_failures)。
    AIPOS-F150 件②: owner_needed 类结束且 end_codes 非空并全部在 routine_rejections.codes 内 = advisor_fix(另带 codes / fix);
    earlier_end_codes = 本卡更早各次运行的 end_codes(旧→新; 未结束 = None), 同一例行码连续 repeat_owner_needed 次 =
    owner_needed(routine_fix_exhausted)。"""
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
        probed_from, probe_note = "", ""
        if action != "advisor_fix":
            # AIPOS-F204 件③: 以 artifact_invalid 停线的运行(F204 前执行体交回报告头错误即此)= 以产物入口同一校验复核本卡此刻的交回;
            # 报告头可纠正 = 视同例行结束原因 return_report_form_error(原命令重跑 loop 即同会话修报告头)
            as_reason, probe_note = _return_header_probe(view, rules, governance_root, reason)
            if as_reason:
                probed_from, reason, action = reason, as_reason, "advisor_fix"
        mode_note = ""
        if any(reason in (m.get("by_end_reason") or {}) for m in ((rules.get("by_execution_mode") or {}).get("modes") or {}).values()):
            # AIPOS-F156 件①: 本卡执行方式覆盖(如子 agent 模式等交回超时 = 例行); 判不出 = 原口径, 原因进 detail
            mode, mode_note = run_execution_mode(governance_root, view)
            action = str((_execution_rule(rules, mode).get("by_end_reason") or {}).get(reason) or action)
            if mode:
                view = {**view, "execution_mode": mode}
        exempt = set(rules.get("consecutive_failures_exempt") or [])  # AIPOS-F231 件⑥: 驱动方主动上限(--max-steps)不算失败
        if action == "investigate" and earlier_end_reasons is not None and reason not in exempt:
            streak = 1
            for earlier in reversed(earlier_end_reasons):
                if earlier is None or str(earlier) in exempt or rules["by_end_reason"].get(str(earlier)) != "investigate":
                    break
                streak += 1
            if streak >= int(rules["consecutive_failures_owner_needed"]):
                return {"action": "owner_needed", "reason": "consecutive_failures",
                        "detail": f"{rules['owner_reasons']['consecutive_failures']}(连续 {streak} 次, 本次 {reason}: {message})",
                        "hint": str(rules["investigate_hint"]).format(**fill)}
        if action == "card_done_take_next":
            return {"action": action, "reason": reason, **_take_next(view, rules, governance_root, message)}
        if action == "advisor_fix":  # AIPOS-F152 件①: 结束原因本身属例行(routine_rejections.end_reasons, 如 deploy_changed)
            routine = _routine_end_reason_action(view, rules, governance_root, reason, message, earlier_end_reasons)
            if probed_from:
                routine = {**routine, "end_reason": probed_from,
                           "detail": f"{routine['detail']}(运行以 {probed_from} 结束; 复核本卡此刻交回 = 报告头可纠正, AIPOS-F204)"}
            return {**routine, "execution_mode": view["execution_mode"]} if view.get("execution_mode") else routine
        if action == "owner_needed" and reason in (rules["envelope_renew"].get("end_reasons") or []):
            return _envelope_renew_action(view, rules, reason, message, fill)  # AIPOS-F166 件②: 信封续签专门出口
        if action == "owner_needed":
            review = _advisor_review_close(view, rules, governance_root, reason)
            if review is not None:  # AIPOS-F207 件③: 推导核建议 = 顾问复核结案 → 顾问自处理, 不进 Owner 收件箱
                return review
            probe_note = ""
            if not view.get("end_codes"):  # AIPOS-F195 件③: 落账被拒而无码 = N6 同一提交口预演复核(拒因仅为产物槽代码文件 = 例行)
                probed, probe_note = _landing_probe_codes(view, rules, governance_root, reason)
                if probed:
                    view = {**view, "end_codes": probed}
            routine = _routine_action(view, rules, governance_root, reason, message, earlier_end_codes)
            if routine is not None:
                return routine
            return {"action": action, "reason": reason, "detail": f"{rules['owner_reasons'][reason]}" + (f" — {message}" if message else "")
                    + (f"({mode_note})" if mode_note else "") + (f"({probe_note})" if probe_note else ""),
                    "hint": str(rules["investigate_hint"]).format(**fill)}
        if action == "continue_wait":  # AIPOS-F171B 件①: already_running = 另一个 loop 在跑, 继续等它(提示同运行中)
            return {"action": action, "reason": reason, "detail": message or reason, "hint": str(rules["wait_hint"]).format(**fill)}
        return {"action": action, "reason": reason, "detail": (message or reason) + (f"({probe_note})" if probe_note else ""),
                "hint": str(rules["investigate_hint"]).format(**fill)}
    if state not in rules["by_state"]:
        raise LoopRunRecordError(f"状态 {state!r} 不在 verbs.schema {STATUS_VERB}.next_action.by_state")
    action = str(rules["by_state"][state])
    if action == "continue_wait":
        cur = view.get("current_step") or {}
        lock = view.get("resource_lock") or {}
        if lock.get("state") == "waiting" and lock.get("holder") and cur.get("kind") == _resource_wait_kind():
            # AIPOS-F179 item 2: queued behind an exclusive-resource holder (detail = holder line from the run record)
            from tools.aipos_cli.loop_driver import load_loop_contract

            line = resource_wait_line(lock["holder"], resource_lock_declaration(load_loop_contract()))
            return {"action": action, "reason": "resource_wait", "detail": line, "hint": str(rules["wait_hint"]).format(**fill)}
        if cur.get("kind") == "wait" and cur.get("mode") == "manual":
            what = cur.get("hint") or f"等工位产物 {cur.get('waiting_for')}"
            mode, _note = run_execution_mode(governance_root, view)
            kickoff = _execution_rule(rules, mode).get("kickoff_detail")
            if kickoff:  # AIPOS-F156 件①: 子 agent 模式 = 顾问派子 agent 开工(不找 Owner), 前缀读声明
                return {"action": action, "reason": "manual_kickoff", "execution_mode": mode,
                        "detail": str(kickoff).replace("{hint}", str(what)), "hint": str(rules["wait_hint"]).format(**fill)}
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


def _advisor_review_close(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, reason: str) -> dict[str, Any] | None:
    """AIPOS-F207 件③(声明 next_action.advisor_review_close): 所列结束原因的运行, 推导核此刻对本卡的建议动词 ∈ verbs = advisor_fix
    (reason advisor_review_close; hint = 推导核给的顾问命令加 --dry-run)。推导唯一 next_resolver.derive_next_step; 不适用 / 推导核已不给该动词 = None。"""
    spec = rules.get("advisor_review_close")
    task_id = str(view.get("task_id") or "").strip()
    if not isinstance(spec, dict) or reason not in (spec.get("end_reasons") or []) or not task_id:
        return None
    from tools.aipos_cli.next_resolver import derive_next_step

    try:
        derivation = derive_next_step(task_id, Path(governance_root))
    except (OSError, ValueError, LookupError) as exc:  # 推导不了 = 不改判(原 owner_needed 照旧, 原因见 lybra next)
        return {"action": "owner_needed", "reason": reason, "end_reason": reason,
                "detail": f"{rules['owner_reasons'][reason]}(顾问复核结案判定推导出错: {type(exc).__name__}: {exc})",
                "hint": str(rules["investigate_hint"]).format(task_id=task_id, governance_root=str(governance_root))}
    command = str(derivation.get("advisor_command") or "").strip()
    if str(derivation.get("verb") or "") not in spec["verbs"] or not command:
        return None
    fill = {"task_id": task_id, "command": command, "suggested_action": str(derivation.get("suggested_action") or ""),
            "governance_root": str(governance_root)}
    return {"action": "advisor_fix", "reason": "advisor_review_close", "end_reason": reason,
            "detail": str(spec["detail"]).format(**fill), "hint": str(spec["hint"]).format(**fill)}


def _resource_wait_kind() -> str:
    from tools.aipos_cli.loop_driver import load_loop_contract

    return str(resource_lock_declaration(load_loop_contract())["wait_kind"])


def _envelope_renew_action(view: dict[str, Any], rules: dict[str, Any], reason: str, message: str, fill: dict[str, str]) -> dict[str, Any]:
    """AIPOS-F166 件②: envelope_exhausted / envelope_expired → owner_needed(envelope_renew): detail = 事由 + 结束时门侧用量真相(运行记录
    envelope_state: 用量 / 上限 / 到期), hint = 续签命令(policy_id 按该驱动方已有最大序号 +1)。旧记录无 envelope_state = 事由 + 结束原文,
    hint = investigate_hint(不猜命令)。"""
    st = view.get("envelope_state") or {}
    parts = [str(rules["owner_reasons"][reason])]
    if st:
        parts.append(f"信封 {st.get('policy_id')} 已放行 {st.get('used')}/{st.get('max_tasks')} 张业务卡, 到期 {st.get('expires_at')}"
                     f"({'额度已用尽' if st.get('state') == 'exhausted' else '已过期' if st.get('state') == 'expired' else st.get('state')})"
                     f"; 续签新信封 {st.get('next_policy_id')}")
        if st.get("quota_line"):
            parts.append(str(st["quota_line"]).strip())  # AIPOS-F192: 续签额度 = 业务卡张数(本卡所需 / 新卡数)
        if st.get("note"):
            parts.append(f"注: {st['note']}")
    elif message:
        parts.append(message)
    out = {"action": "owner_needed", "reason": str(rules["envelope_renew"]["reason"]), "end_reason": reason, "detail": " — ".join(parts),
           "hint": str(st.get("renew_command") or rules["investigate_hint"].format(**fill))}
    if st:
        out["envelope_state"] = dict(st)
    return out


def _landing_probe_codes(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, reason: str) -> tuple[list[str], str]:
    """AIPOS-F195 件③(声明 routine_rejections.landing_probe): 落账被拒且无码的运行 → 以 N6 同一提交口(governance_commit, task 范围
    --dry-run; 只读)复核本卡: 预演 PASS 且将排除产物槽代码文件 = [声明码](拒因仅为产物槽代码文件, 原命令重跑即走 N6 处置);
    否则 []。预演出错 = [] + 原文(调用方写进 detail, owner_needed 照旧)。返回 (码列表, 说明)。"""
    probe = (rules.get("routine_rejections") or {}).get("landing_probe")
    if not isinstance(probe, dict) or reason not in (probe.get("end_reasons") or []):
        return [], ""
    task_id = str(view.get("task_id") or "").strip()
    if not task_id:
        return [], ""
    from tools.aipos_cli.governance_commit import N6_LANDING_ACTOR_PLACEHOLDER, governance_commit

    try:
        result = governance_commit(Path(governance_root), task_id, str(view.get("driver") or "").strip() or N6_LANDING_ACTOR_PLACEHOLDER,
                                   dry_run=True, push=False)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return [], f"N6 预演复核出错: {type(exc).__name__}: {exc}"
    if str(result.get("verdict")) == "PASS" and result.get("excluded_code_files"):
        from tools.aipos_cli.governance_commit import n6_landing_declaration
        from tools.schema_loader import SchemaLoadError

        code = str((n6_landing_declaration().get("slot_code_files") or {}).get("probe_code") or "")
        if code not in ((rules.get("routine_rejections") or {}).get("codes") or {}):
            raise SchemaLoadError(f"transitions nodes.N6.landing.slot_code_files.probe_code {code!r} 未在 verbs.{STATUS_VERB}"
                                  f".next_action.routine_rejections.codes 声明修法")
        return [code], ""
    return [], ""


def _report_cards(view: dict[str, Any]) -> list[str]:
    """AIPOS-F232 件②: 结束时报告可能属的卡(去重有序): 最后一个已落定步的卡(读不出的审计报告挂审计卡)、本卡。"""
    return [c for c in dict.fromkeys([str(view.get("last_step_card") or "").strip(), str(view.get("task_id") or "").strip()]) if c]


def _report_header_check(governance_root: Path, view: dict[str, Any], *, unreadable_only: bool) -> tuple[str, dict[str, Any] | None]:
    """依次以产物入口同一校验(artifact_ingest.validate_task_artifact, 只读)复核 _report_cards 各卡此刻的报告: 首个 header_problem 可纠正者 =
    (卡, 校验结果); unreadable_only = 只认报告头读不出(INGEST_FRONTMATTER_UNREADABLE)。无 = ("", None)。复核出错向上(调用方点名)。"""
    from tools.aipos_cli.artifact_ingest import validate_task_artifact

    for card in _report_cards(view):
        check = validate_task_artifact(card, Path(governance_root))
        problem = check.get("header_problem")
        if not isinstance(problem, dict) or not problem.get("correctable"):
            continue
        if unreadable_only and check.get("category") != "INGEST_FRONTMATTER_UNREADABLE":
            continue
        return card, check
    return "", None


def _return_header_probe(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, reason: str) -> tuple[str, str]:
    """AIPOS-F204 件③(声明 routine_rejections.return_header_probe): 以所列结束原因结束的运行 → 以产物入口同一校验
    (artifact_ingest.validate_task_artifact 的 header_problem; 只读)复核本卡此刻的交回: 报告头可纠正 = (视同结束原因, "");
    否则 ("", "")。复核出错 = ("", 原文)(调用方写进 detail, 照旧判)。
    AIPOS-F232 件②: 结束原因 frontmatter_unreadable(F232 前 loop 对报告头 YAML 错即此)= 复核最后一步的卡与本卡的报告, 报告头读不出 =
    按该卡角色视同其修报告头结束原因(审计卡 = launch.report_fix.end_reason, 执行卡 = report_fix.executor.end_reason; advisor_fix 例行修法),
    不再 investigate; 读不出的不是报告(卡面 / 记录)= 照旧。"""
    probe = (rules.get("routine_rejections") or {}).get("return_header_probe")
    if not isinstance(probe, dict) or reason not in (probe.get("end_reasons") or []):
        return "", ""
    task_id = str(view.get("task_id") or "").strip()
    if not task_id:
        return "", ""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.loop_driver import load_loop_contract, report_fix_declaration

    unreadable_only = reason == "frontmatter_unreadable"
    probe_view = view if unreadable_only else {**view, "last_step_card": ""}  # F204 口径: 只复核本卡交回
    try:
        card, check = _report_header_check(Path(governance_root), probe_view, unreadable_only=unreadable_only)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return "", f"{'报告' if unreadable_only else '交回'}报告头复核出错: {type(exc).__name__}: {exc}"
    if check is None:
        return "", ""
    role = "auditor" if card != task_id and is_audit_card(card) else "executor"
    return str(report_fix_declaration(load_loop_contract(), role)["end_reason"]), ""


def header_fix_prompt_value(governance_root: Path, task_id: str, view: dict[str, Any] | None = None) -> str:
    """AIPOS-F204 件③: 修法占位 {header_fix_prompt} 的实值 = 按本卡此刻交回渲染的修交回报告头指令(唯一渲染
    loop_driver.executor_report_fix_prompt; 判定 artifact_ingest.return_header_problem); 此刻已无可纠正问题 / 复核出错 = 一句说明。
    AIPOS-F232 件②: view 给出 = 复核最后一步的卡与本卡(读不出的审计报告挂审计卡); 报告头读不出 = 修报告头(读不出)指令
    (唯一渲染 loop_driver.report_unreadable_fix_prompt, 含解析错行与原因)。"""
    from tools.aipos_cli.audit_derivation import is_audit_card
    from tools.aipos_cli.loop_driver import executor_report_fix_prompt, load_loop_contract, report_unreadable_fix_prompt

    probe_view = view or {"task_id": task_id}
    try:
        card, check = _report_header_check(Path(governance_root), {**probe_view, "task_id": task_id}, unreadable_only=False)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return f"(复核本卡报告出错, 给不出指令: {type(exc).__name__}: {exc})"
    if check is None:
        return "(本卡此刻的报告头已无可纠正问题(可解析, 交回键齐); 报告头 commit_sha 形式错按上一条修法, 其余直接用原命令续跑 loop)"
    problem = check["header_problem"]
    if check.get("category") == "INGEST_FRONTMATTER_UNREADABLE":
        role = "auditor" if card != task_id and is_audit_card(card) else "executor"
        return report_unreadable_fix_prompt(load_loop_contract(), str(check.get("path") or ""), problem, role)
    return executor_report_fix_prompt(load_loop_contract(), str(check.get("path") or ""), problem)


def _routine_action(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, reason: str, message: str,
                    earlier_end_codes: list[Any] | None) -> dict[str, Any] | None:
    """AIPOS-F150 件②: owner_needed 类结束 → 例行门拒判定。end_codes 为空或含非例行码 = None(调用方照旧 owner_needed)。
    全部例行: 同一例行码在本卡最近连续各次运行(含本次)中出现 ≥ repeat_owner_needed 次 = owner_needed(routine_fix_exhausted);
    否则 advisor_fix, hint = 各码 fix 按序渲染 + resume_hint(声明 routine_rejections)。修法渲染所需事实(卡面 / 工作树)取不到 =
    owner_needed 并写明缘由(给不出确定修法宁停不猜)。"""
    routine = rules["routine_rejections"]
    codes = [str(c) for c in (view.get("end_codes") or [])]
    if not codes or any(c not in routine["codes"] for c in codes):
        return None
    task_id = str(view.get("task_id") or "")
    fill = {"task_id": task_id, "governance_root": str(governance_root)}
    limit = int(routine["repeat_owner_needed"])
    streaks: dict[str, int] = {}
    for code in codes:
        streak = 1
        for earlier in reversed(earlier_end_codes or []):
            if earlier is None or code not in earlier:
                break
            streak += 1
        streaks[code] = streak
    worst = max(codes, key=lambda c: streaks[c])
    tally = ", ".join(f"{c} 第 {streaks[c]}/{limit} 次" for c in codes)
    if streaks[worst] >= limit:
        return {"action": "owner_needed", "reason": "routine_fix_exhausted", "codes": codes,
                "detail": f"{rules['owner_reasons']['routine_fix_exhausted']}(本卡 {worst} 连续 {streaks[worst]} 次; 本次 {reason}: {message})",
                "hint": str(rules["investigate_hint"]).format(**fill)}
    try:
        values = _routine_fill(view, routine, governance_root)
    except _RoutineFixUnrenderable as exc:
        return {"action": "owner_needed", "reason": reason, "codes": codes,
                "detail": f"{rules['owner_reasons'][reason]}(例行拒因 {', '.join(codes)} 的修法渲染不了: {exc})" + (f" — {message}" if message else ""),
                "hint": str(rules["investigate_hint"]).format(**fill)}
    fix: list[str] = []
    for code in codes:
        fix += [_render_routine(step, values, f"codes.{code}.fix") for step in routine["codes"][code]["fix"]]
    resume = _render_routine(routine["resume_hint"], values, "resume_hint")
    hint = _routine_hint(fix, resume)
    return {"action": "advisor_fix", "reason": reason, "codes": codes, "fix": fix, "resume": resume,
            "detail": f"例行门拒 {tally}: 修法固定, 顾问按下一条自处理, 不问 Owner" + (f" — {message}" if message else ""),
            "hint": hint}


def _routine_end_reason_action(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, reason: str, message: str,
                               earlier_end_reasons: list[Any] | None) -> dict[str, Any]:
    """AIPOS-F152 件①: 例行结束原因(routine_rejections.end_reasons)→ advisor_fix, hint = 该原因 fix 逐条渲染 + resume_hint(续跑命令
    占位值与例行门拒同一实现 _resume_fill); 本卡最近连续各次运行以同一原因结束 ≥ repeat_owner_needed = owner_needed(routine_fix_exhausted)。"""
    routine = rules["routine_rejections"]
    task_id = str(view.get("task_id") or "")
    fill = {"task_id": task_id, "governance_root": str(governance_root)}
    limit = int(routine["repeat_owner_needed"])
    streak = 1
    for earlier in reversed(earlier_end_reasons or []):
        if earlier != reason:
            break
        streak += 1
    if streak >= limit:
        return {"action": "owner_needed", "reason": "routine_fix_exhausted", "codes": [],
                "detail": f"{rules['owner_reasons']['routine_fix_exhausted']}(本卡连续 {streak} 次以 {reason} 结束; 本次: {message})",
                "hint": str(rules["investigate_hint"]).format(**fill)}
    values = _resume_fill(view, governance_root)
    from tools.aipos_cli.next_resolver import (audit_blocked_declaration, audit_blocked_fix_values, candidate_fail_end_reasons,
                                               candidate_fail_fix_values)

    if reason == str(audit_blocked_declaration()["end_reason"]):
        # AIPOS-F179 item 3: BLOCK reason excerpt + re-audit command (single rendering next_resolver.reaudit_command)
        values = {**values, **audit_blocked_fix_values(governance_root, task_id, actor=str(view.get("driver") or ""),
                                                       policy_ref=str(view.get("envelope") or ""))}
    elif reason in candidate_fail_end_reasons():
        # AIPOS-F211 item 4: FAIL reason excerpt + source card / candidate + supersede command (next_resolver.candidate_fail_fix_values);
        # AIPOS-F227: + fail scope note / same-candidate rework exit (rework command --actor = run record driver)
        values = {**values, **candidate_fail_fix_values(governance_root, task_id, actor=str(view.get("driver") or ""))}
    if any("{header_fix_prompt}" in str(step) for step in routine["end_reasons"][reason]["fix"]):
        values = {**values, "header_fix_prompt": header_fix_prompt_value(governance_root, task_id, view)}  # AIPOS-F204 件③ / F232 件②
    if any("{evidence_amend}" in str(step) for step in routine["end_reasons"][reason]["fix"]):
        # AIPOS-F233 件③: 运行证据声明错的修正出口(拒因 + 受限 amend 命令原文 + 建议改法), 唯一渲染 artifact_ingest.evidence_declaration_fix_line
        from tools.aipos_cli.artifact_ingest import evidence_declaration_fix_line

        values = {**values, "evidence_amend": evidence_declaration_fix_line(governance_root, task_id)}
    where = f"end_reasons.{reason}.fix"
    fix = [_render_routine(step, values, where) for step in routine["end_reasons"][reason]["fix"]]
    resume = _render_routine(routine["resume_hint"], values, "resume_hint")
    hint = _routine_hint(fix, resume)
    return {"action": "advisor_fix", "reason": reason, "codes": [], "fix": fix, "resume": resume,
            "detail": f"例行结束 {reason} 第 {streak}/{limit} 次: 修法固定, 顾问按下一条自处理, 不问 Owner" + (f" — {message}" if message else ""),
            "hint": hint}


def _routine_hint(fix: list[str], resume: str) -> str:
    """修法逐条编号 + 末行续跑命令(例行门拒与例行结束原因同一渲染)。"""
    return "\n".join([*(f"{i}) {step}" for i, step in enumerate(fix, 1)), resume])


def _resume_fill(view: dict[str, Any], governance_root: Path) -> dict[str, str]:
    """续跑命令占位实值(task_id / governance_root / actor_arg / envelope_arg; 运行记录 driver / envelope)——例行门拒与例行结束原因同一实现。"""
    driver, envelope = str(view.get("driver") or "").strip(), str(view.get("envelope") or "").strip()
    return {"task_id": str(view.get("task_id") or ""), "governance_root": str(governance_root),
            "actor_arg": f" --actor {driver}" if driver else "", "envelope_arg": f" --envelope {envelope}" if envelope else ""}


class _RoutineFixUnrenderable(ValueError):
    """例行修法所需事实(卡面 / 卡工作树落点)取不到。"""


def _render_routine(template: str, values: dict[str, str], where: str) -> str:
    from tools.schema_loader import SchemaLoadError

    try:
        return str(template).format_map(values)
    except (KeyError, IndexError, ValueError) as exc:
        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.routine_rejections.{where} 占位不在 placeholders 声明内: {exc}") from exc


def _routine_fill(view: dict[str, Any], routine: dict[str, Any], governance_root: Path) -> dict[str, str]:
    """修法占位实值(键 = routine_rejections.placeholders): 全经既有唯一实现——卡面 task_loader.find_task_card;
    分支 next_resolver.card_branch_name; 期望基座 next_resolver.card_expected_base; 工作树 next_resolver.card_worktree_location;
    合入命令 board_adapter.base_sync_command。"""
    from tools.aipos_cli.board_adapter import base_sync_command
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.next_resolver import card_branch_name, card_expected_base, card_worktree_location
    from tools.aipos_cli.task_loader import AmbiguousTaskCard, find_task_card
    from tools.aipos_cli.workspace_config import CardRepoUnresolved

    task_id = str(view.get("task_id") or "")
    try:
        path, _state = find_task_card(Path(governance_root), task_id)
        if path is None:
            raise _RoutineFixUnrenderable(f"队列中找不到卡 {task_id}")
        fm, _body = require_frontmatter(path)
        _repo, worktree = card_worktree_location(Path(governance_root), task_id, fm)
    except (AmbiguousTaskCard, FrontmatterReadError, CardRepoUnresolved) as exc:
        raise _RoutineFixUnrenderable(f"{type(exc).__name__}: {exc}") from exc
    branch, base = card_branch_name(task_id), card_expected_base(fm)
    values = {**_resume_fill(view, governance_root), "branch": branch, "base": base,  # AIPOS-F152: 续跑占位与例行结束原因同一实现
              "worktree": str(worktree), "base_sync_command": base_sync_command(branch, base)}
    undeclared = sorted(set(values) - set(routine["placeholders"]))
    if undeclared:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"verbs.{STATUS_VERB}.next_action.routine_rejections.placeholders 未声明 {undeclared}")
    return values


def _take_next(view: dict[str, Any], rules: dict[str, Any], governance_root: Path, message: str) -> dict[str, Any]:
    """AIPOS-F136 × F133: 结案后取下一张 —— 接 F133「下一张」唯一出口(next_resolver.scan_project_view + pick_next_card, 判据
    verbs.schema lane_view.next_card), 范围 = 本卡 lane(machine_zone.lane_of_card 已解析时; 解析不了 = 全项目, 照 F133
    「不可证明不属本 lane 不隐藏」)。有下一张 = hint 为其 loop 命令(take_next_hint); 无 = hint 为 lane 扫描(take_next_none_hint)。
    AIPOS-F156 件②(gap #140): 归属视角 = 本次运行驱动方 + 本次运行信封(machine_zone.owner_viewer 唯一判定; 信封不限 lane = 总顾问
    看全量): 只取归属本实例 / 归属不明的卡, 共用仓里归属其他实例的卡不取(detail 单列「其他子项目(不取)」)。"""
    from tools.aipos_cli.machine_zone import render_owner_other
    from tools.aipos_cli.next_resolver import pick_next_card, scan_project_view

    lane = view.get("lane") if view.get("lane") and not view.get("lane_error") else None
    fill = {"task_id": str(view.get("task_id") or ""), "governance_root": str(governance_root),
            "lane_arg": f" --lane {lane}" if lane else ""}
    viewer = _run_owner_viewer(Path(governance_root), view)
    # AIPOS-F142 件①: 只要「下一张」= 只扫可当选状态(next_states_only, 同一 scan_project_view 出口), 不为 claimed/blocked 卡做推导
    scan = scan_project_view(Path(governance_root), lane=lane, next_states_only=True, viewer=viewer)
    nxt = pick_next_card(scan["rows"])
    done = message or "已结案并落账"
    # AIPOS-F187 件③: 结案后本卡仍保留的工作树(脏 / 在途 / 承继引用; 只读判定 worktree_cleanup.residual_line)报路径与改动计数
    from tools.aipos_cli.worktree_cleanup import residual_line

    residual = residual_line(Path(governance_root), str(view.get("task_id") or "")) if view.get("task_id") else None
    if residual:
        done = f"{done}; {residual}"
    others = scan.get("owner_other") or []
    tail = f"; {render_owner_other(others)}" if others else ""
    extra = {"owner_view": viewer, "owner_other": others} if viewer else {}
    if nxt is None:
        return {"detail": f"{done}; 下一张可推进卡: 无{'(lane ' + lane + ')' if lane else ''}{tail}", "next_card": None,
                "hint": str(rules["take_next_none_hint"]).format(**fill), **extra}
    card = {"task_id": nxt.get("task_id"), "lane": nxt.get("lane"), "priority": nxt.get("priority")}
    return {"detail": f"{done}; 下一张可推进卡: {card['task_id']} lane={card['lane'] or '-'} priority={card['priority'] or '-'}{tail}",
            "next_card": card, "hint": str(rules["take_next_hint"]).format(**{**fill, "next_task_id": card["task_id"]}), **extra}


def _run_owner_viewer(governance_root: Path, view: dict[str, Any]) -> dict[str, Any] | None:
    """运行的归属视角: 驱动方(运行记录 driver)+ 本次运行信封(运行记录 envelope, autonomy_policy.load_policy 读; 读不出 = 按该实例的
    有效信封判总顾问)。无驱动方(旧记录)= None(不按归属分, 行为不变)。"""
    from tools.aipos_cli.autonomy_policy import load_policy
    from tools.aipos_cli.machine_zone import owner_viewer

    driver = str(view.get("driver") or "").strip()
    if not driver:
        return None
    envelope = str(view.get("envelope") or "").strip()
    policy = load_policy(Path(governance_root), envelope) if envelope else None
    return owner_viewer(Path(governance_root), actor=driver, policy=policy)


class StatusUsageError(ValueError):
    """--wait 用法错(缺 --task-id / 秒数越界): 零等待, 出口 usage。"""


def wait_for_next_action(governance_root: Path, task_id: str, seconds: float, *, contract: dict[str, Any] | None = None,
                         sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                         interval: float | None = None, lane: str | list[str] | None = None,
                         deploy_guard: Any | None = None) -> dict[str, Any]:
    """`loop status --task-id <ID> --wait <秒>`: 经 agent_watch_fs.run_fs_watch(唯一等待原语)有界等待, 直到本卡最近一次运行的
    下一动作 ≠ continue_wait(expect_ready 每轮重读记录 + judge_run + next_action; 进程死 / 停滞无需文件变化)或到时; 返回当时的
    loop_status 报告 + wait 段 {requested_seconds, waited_seconds, outcome: ready|timeout}。上限与间隔读 verbs.schema。
    AIPOS-F152 件①: deploy_guard(缺省 = 本进程的 deploy_runtime.DeployGuard)经 run_fs_watch 每轮比对部署; 等待中部署一变 =
    outcome deploy_changed, 报告另附 deploy_changed 段(新旧提交 + 重跑提示), 调用方按 exit_codes.deploy_changed 退出。"""
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

    from tools.aipos_cli.deploy_runtime import DeployGuard

    guard = deploy_guard if deploy_guard is not None else DeployGuard.for_running_process()
    started = clock()
    rc = None
    if root.is_dir():  # 落点根尚不存在 = 本项目从未跑过 loop: 无可等(no_run), 只读不建目录
        args = SimpleNamespace(workspace_root=str(root), expect=[f"{task_id}/looprun_*.md"], timeout=float(seconds),
                               interval=float(interval if interval is not None else step), events="expect", stream=False,
                               run_log=None, end_pattern=None, stall_secs=None, health=None)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = run_fs_watch(args, sleeper=sleeper, clock=clock, expect_ready=_ready, deploy_guard=guard)
        if rc not in (declared_exit_code("lybra_agent_watch", "change"), declared_exit_code("lybra_agent_watch", "timeout"),
                      declared_exit_code("lybra_agent_watch", "deploy_changed")):
            raise LoopRunRecordError(f"等待原语 agent watch 异常退出 {rc}: {sink.getvalue().strip()}")
    report = loop_status(governance_root, task_id, contract=contract, lane=lane)  # 返回当时状态(到时 / 就绪均重读一次, 不用缓存)
    outcome = "ready" if (report.get("next_action") or {}).get("action") not in (None, "continue_wait") else "timeout"
    change = guard.check() if rc == declared_exit_code("lybra_agent_watch", "deploy_changed") else None
    if change:
        from tools.aipos_cli.deploy_runtime import change_summary

        outcome = "deploy_changed"
        report["deploy_changed"] = {**change, "message": f"{change_summary(change)}; 本命令运行的是旧版本代码, 等待已停下, "
                                                         f"用原命令重跑(lybra loop status --task-id {task_id} --wait <秒>)"}
    report["wait"] = {"requested_seconds": float(seconds), "waited_seconds": round(clock() - started, 1), "outcome": outcome,
                      "watch_root": str(root), "max_seconds": max_seconds}
    return report


def wait_regression_baseline(governance_root: Path, commit: str, seconds: float) -> dict[str, Any]:
    """AIPOS-F188 件①: `regression baseline --wait <秒>`(判定唯一实现 post_merge_regression.audit_baseline; 本函数只做等待, 放在本模块 =
    loop 有界等待所在处, 且不把等待原语的导入闭包带进 finalize 合并后预载集)——先判一次; pending 时经唯一等待原语 agent_watch_fs.run_fs_watch 有界等待
    (expect = 进行中记录所在目录的记录文件, 就绪谓词 = 该 sha 不再 pending; stop_when = 回归进程已不在; 禁 sleep 自旋), 醒来重判。
    到点仍在跑 = self_run_required(原因写明等了多久)。非 pending = 不等待原样返回。结果另带 waited {requested_seconds, waited_seconds, outcome}。
    秒数越界 = ValueError(声明 wait.max_seconds)。"""
    import contextlib
    import io
    import time

    from tools.aipos_cli.agent_watch_fs import run_fs_watch
    from tools.aipos_cli.loop_driver import _watch_args
    from tools.aipos_cli.post_merge_regression import (
        AUDIT_BASELINE_PENDING, AUDIT_BASELINE_RECORDED, AUDIT_BASELINE_SELF_RUN, BASELINE_VERB, EVENT_TYPE, audit_baseline,
        baseline_declaration, regression_states,
    )

    wait = baseline_declaration()["wait"]
    seconds = float(seconds)
    if not 0 < seconds <= float(wait["max_seconds"]):
        raise ValueError(f"--wait 须在 (0, {wait['max_seconds']}] 秒内(verbs.schema {BASELINE_VERB}.wait.max_seconds), 实为 {seconds:g}")
    result = audit_baseline(governance_root, commit)
    if result["status"] != AUDIT_BASELINE_PENDING:
        result["waited"] = {"requested_seconds": seconds, "waited_seconds": 0, "outcome": "not_pending"}
        return result
    pending = result["pending"]
    record = Path(governance_root) / str(pending["record"])
    entry = next((e for e in regression_states(Path(governance_root), sha=commit) if e.get("state") == "running"), {})
    fingerprint = _process_fingerprint_of(record, EVENT_TYPE)

    def _concluded(_matched: list[str]) -> bool:
        return audit_baseline(governance_root, commit)["status"] != AUDIT_BASELINE_PENDING

    interval = min(float(wait["interval_seconds"]), max(0.05, seconds / 10))
    args = _watch_args(record.parent, ["*.md"], max_wait=seconds, interval=interval)
    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):
        run_fs_watch(args, expect_ready=_concluded,
                     stop_when=lambda: not process_alive(entry.get("pid"), fingerprint) if fingerprint else False)
    waited = int(round(time.monotonic() - started))
    result = audit_baseline(governance_root, commit)
    if result["status"] == AUDIT_BASELINE_PENDING:
        still = result.pop("pending")
        result.pop("wait_command", None)
        result.update(status=AUDIT_BASELINE_SELF_RUN, reasons=[
            f"--wait {seconds:g}s 到点仍在跑(卡 {still.get('task_id')} 记录 {still.get('record')}, pid {still.get('pid')}, "
            f"起于 {still.get('started_at')}, 预计最迟 {still.get('deadline')} 完成)——不再等, main 侧自跑"])
        result["waited"] = {"requested_seconds": seconds, "waited_seconds": waited, "outcome": "still_running"}
        return result
    concluded = result["status"] == AUDIT_BASELINE_RECORDED or any(
        e.get("state") == "concluded" for e in regression_states(Path(governance_root), sha=commit))
    outcome = "concluded" if concluded else "process_gone"
    result["waited"] = {"requested_seconds": seconds, "waited_seconds": waited, "outcome": outcome}
    return result


def _process_fingerprint_of(record: Path, event_type: str) -> Any:
    """进行中记录的进程启动时刻指纹(F166 前 finalization 记录无 = None: 不以进程消失为醒点, 只等记录结论或到点)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter

    try:
        meta, _body = require_frontmatter(record)
    except (FrontmatterReadError, OSError) as exc:
        raise ValueError(f"进行中记录读不出: {record}: {exc}") from exc
    pmr = meta.get(event_type) if isinstance(meta.get(event_type), dict) else {}
    return pmr.get("process_fingerprint")


def model_label(resolution: Any) -> str:
    """AIPOS-F169: 拉起模型一处渲染(loop status 拉起行 / set-model 影响工位 / check-workstation 同用):
    声明了 = `<provider>/<id>(来源)`; 未传 --model = 来源说明(pi 缺省)。"""
    if not isinstance(resolution, dict):
        return "?"
    if resolution.get("model"):
        return f"{resolution['model']}({resolution.get('source')})"
    return str(resolution.get("source") or "pi 缺省")


def _dur(seconds: Any) -> str:
    if not isinstance(seconds, int):
        return "?"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def render_status(report: dict[str, Any], decl_states: dict[str, Any]) -> str:
    from tools.aipos_cli.machine_zone import lane_filter_label, lane_view_declaration, render_visible_summary  # F139: 仓集合

    runs = report["runs"]
    summary = render_visible_summary(report)  # AIPOS-F141: 可见卡汇总行(四视图同一渲染)
    if not runs:
        what = f"卡 {report['task_id']} 无 loop 运行记录" if report.get("task_id") else "本项目无未结束的 loop 运行"
        if report.get("lane_filter"):
            what += f"(lane {lane_filter_label(report['lane_filter'])})"
        unfinished = [row["line"] for row in report.get("post_merge_regression_unfinished") or []]  # AIPOS-F166 件③
        return "\n".join([f"{what}(落点 {report['loop_runs_root']})", *unfinished, *summary])
    marker = str(lane_view_declaration()["visible_cards"]["frozen_marker"])
    out: list[str] = []
    for v in runs:
        state = v["state"]
        out.append(f"loop 运行 {v['run_id']}  [{state}] {decl_states.get(state, '')}" + (f" {marker}" if v.get("frozen") else ""))
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
            model = f" 模型 {model_label(la['model'])}" if la.get("model") else ""  # AIPOS-F169: 存量记录无 model = 不显示
            if la.get("session_id"):  # AIPOS-F171B 件②: 固定会话与续接次数(存量记录无 = 不显示)
                model += f" 会话 {la['session_id']} 续接 {la.get('resume') or 0}/{la.get('resume_max') or 0}"
            if la.get("ended_at"):
                out.append(f"  拉起 {la['harness']} {la['card']}{model} pid={la['pid']} 已收尾: outcome={la.get('outcome')} "
                           f"returncode={la.get('returncode')} termination={la.get('termination')}")
            else:
                alive = {True: "存活", False: "已不在"}.get(la.get("alive"), "未探活")
                out.append(f"  拉起 {la['harness']} {la['card']}{model} @ {la.get('host') or '本机'}:{la.get('workstation')} "
                           f"pid={la['pid']} pgid={la['pgid']} {alive}"
                           + (f"({la['probe_note']})" if la.get("probe_note") else "") + f", 已运行 {_dur(la.get('running_seconds'))}, "
                           f"距最近输出 {_dur(la.get('idle_seconds'))}")
            out.append(f"  活动: 事件 {la.get('events')} 条; 最近 {la.get('last_activity_kind') or '(无)'} @ "
                       f"{la.get('last_activity_at') or '-'}; 计数 {la.get('counts') or {}}")
        if state == "ended":
            out.append(f"  已结束 {v.get('ended_at')}: outcome={v.get('outcome')} exit={v.get('exit_code')} "
                       f"reason={v.get('end_reason')}" + (f" — {v['end_message']}" if v.get("end_message") else ""))
        dep = v.get("deploy") or {}
        if dep.get("changed_to") or dep.get("changed_to_detail"):  # AIPOS-F152 件①: 运行记录 deploy 段(新旧提交)
            out.append(f"  部署: 本运行快照 {dep.get('release')} commit {dep.get('commit')} → 现部署 commit "
                       f"{dep.get('changed_to') or dep.get('changed_to_detail')}({dep.get('changed_at')})")
        if dep.get("self_deployed_to"):  # AIPOS-F155 件③: 本 loop 自身 finalize 的部署(已采纳为比对基线, 不触发 deploy_changed)
            out.append(f"  部署: 本 loop finalize 自身部署 commit {dep.get('self_deployed_to')}({dep.get('self_deployed_at')}), "
                       f"已采纳为比对基线; 本进程代码仍为快照 {dep.get('release')} commit {dep.get('commit')}")
        if v.get("resource_lock"):  # AIPOS-F179 item 2
            rl = v["resource_lock"]
            holder = rl.get("holder") or {}
            names = ", ".join(rl.get("resources") or [])
            if rl.get("state") == "released":  # AIPOS-F231 件③
                out.append(f"  独占资源 {names}: 已随运行结束释放({rl.get('released_at')}; 运行中曾 {rl.get('state_before_end') or 'held'}"
                           + (f" 自 {rl.get('held_at')}" if rl.get("held_at") else "") + ")")
            elif rl.get("state") == "residual":
                out.append(f"  独占资源 {names}: 停线未清干净, 仍占用(不释放; 未清理项与安全处置见下方停线段)")
            else:
                out.append(f"  独占资源 {names}: {rl.get('state')}"
                           + (f" 自 {rl.get('held_at')}" if rl.get("state") == "held" else f"(请求 {rl.get('requested_at')})")
                           + (f"; 占用者 卡 {holder.get('task_id')} run {holder.get('run_id')} pid {holder.get('pid')} @ {holder.get('host')}"
                              if holder and rl.get("state") == "waiting" else ""))
        diag = (v.get("launch") or {}).get("diagnosis")
        if isinstance(diag, dict) and diag.get("line"):  # AIPOS-F231 件④: 早退真实原因
            out.append(f"  {diag['line']}")
        if v.get("provider_retries"):
            last_retry = v["provider_retries"][-1]
            out.append(f"  模型服务 401 / 429 退避重试: {len(v['provider_retries'])}/{last_retry.get('max')} 次(最近 HTTP {last_retry.get('status')}"
                       f" {last_retry.get('kind')}, 退避 {last_retry.get('backoff_seconds')}s @ {last_retry.get('at')})")
        for chk in [c for c in v.get("context_checks") or [] if c.get("outcome") != "ok"]:  # AIPOS-F231 件⑤
            out.append(f"  开局上下文 {chk.get('card')}: {chk.get('outcome')} 估算 {chk.get('estimate')} / 上限 {chk.get('limit')} 词元"
                       f"(窗口 {chk.get('window')}, 模型 {chk.get('model')})" + (f"; 大节存 {chk['kickoff_file']}" if chk.get("kickoff_file") else ""))
        if v.get("stop"):  # AIPOS-F231 件②
            out.extend(stop_report_lines({**v, "resource_lock": None}))  # 资源行已在上方独占资源行
        if v.get("continues_run"):  # AIPOS-F166 件①
            out.append(f"  续等: 接上一运行 {v['continues_run']}(等待中部署已更新, re-exec 到本运行的部署快照, 从同一等待步续等)")
        if v.get("reexec"):
            rx = v["reexec"]
            out.append(f"  续等: 已 re-exec 到新部署快照 {rx.get('to_release')}(commit {rx.get('to_commit')}, pid {rx.get('pid')} 不变), "
                       f"后续见 continues_run={v['run_id']} 的运行记录" + (f"; re-exec 未成: {rx['failed']}" if rx.get("failed") else ""))
        if v.get("envelope_warning"):  # AIPOS-F166 件②
            out.append(f"  {v['envelope_warning']['line']}")
        for item in v.get("lane_overlaps") or []:  # AIPOS-F188 件③: 起跑时的车道重叠提示(判于 {at})
            out.append(f"  起跑时 {item.get('line')}(判于 {item.get('at')})")
        if v.get("note"):
            out.append(f"  注: {v['note']}")
        if v.get("lane_error"):
            out.append(f"  lane: {v['lane_error']}")
        tool = (v.get("launch") or {}).get("tool_in_flight")
        out.append(f"  停滞判据: 拉起进程存活且 > {v['stall_after_seconds']}s 无输出 = stalled"
                   f"(verbs.schema lybra_loop.run_record.{'stall_after_tool_seconds, 工具在跑' if tool else 'stall_after_seconds'})")
        out.append(f"  记录 {v['record_path']}")
        out.append(f"  日志 {v['log_path']}")
        if v.get("end_codes"):
            out.append(f"  拒因码: {', '.join(v['end_codes'])}")  # AIPOS-F150 件①: 运行记录结构化拒因码
        if v.get("owner_ask"):  # AIPOS-F168 件③: 顾问补写的 Owner 待决结构字段(收件箱同读)
            out.extend(render_owner_ask(v["owner_ask"]))
        if v.get("owner_ask_retracted"):  # AIPOS-F232 件①: 已撤回的请求(留档)
            out.append(render_retracted_ask(v["owner_ask_retracted"]))
        na = v.get("next_action")
        ask_mark = (na or {}).get("owner_ask_inbox") if (na or {}).get("reason") != "owner_ruled" else None
        if isinstance(ask_mark, dict) and ask_mark.get("item_id"):  # AIPOS-F222 件②: 顾问补写的请求已进收件箱(待 Owner 裁决)
            out.append(_note_inbox_line({**ask_mark, "listed": True, "category_label": _inbox_category_label(ask_mark.get("category"))},
                                        note_declaration()["texts"], confirmed=True))
        if na:
            out.append(f"  顾问下一动作: {na['action']}({na['reason']}) — {na['detail']}")
            if isinstance(na.get("owner_reason"), dict):  # AIPOS-F225: 第一档事由
                from tools.aipos_cli.owner_inbox import tier_declaration

                out.append("   " + str(tier_declaration()["texts"]["owner_reason_part"]).format(**na["owner_reason"]))
            first, *rest = str(na["hint"]).splitlines() or [""]
            out.append(f"    下一条: {first}")
            out.extend(f"      {line}" for line in rest)  # AIPOS-F150: advisor_fix 修法多步逐行
            if na.get("escalate_hint"):  # AIPOS-F225: 第二 / 第三档归顾问, 确属第一档才带事由升 Owner
                out.append(f"    {na['escalate_hint']}")
    if report.get("task_id") and report.get("runs_on_record", 0) > 1:
        out.append(f"(该卡共 {report['runs_on_record']} 次运行记录, 上为最近一次)")
    w = report.get("wait")
    if w:
        what = {"ready": "可行动", "deploy_changed": "部署已更新, 等待停下"}.get(w["outcome"], "到时仍在推进")
        out.append(f"(--wait {w['requested_seconds']:g}s: {what}, 实等 {w['waited_seconds']}s)")
    if report.get("deploy_changed"):
        out.append(f"(AIPOS-F152) {report['deploy_changed']['message']}")
    for row in report.get("post_merge_regression_unfinished") or []:  # AIPOS-F166 件③
        out.append(row["line"])
    for rel in report.get("auto_release_records") or []:  # AIPOS-F225 件②: 第二档自动放行(Owner 可在收件箱「已自动放行」撤回)
        out.append(f"第二档自动放行 {rel['auto_release_id']}({rel['kind']} @ {rel['released_at']})" + ("[已被 Owner 撤回]" if rel["revoked"] else "")
                   + f": {rel['basis']}")
    if report.get("supplementary_audit"):  # AIPOS-F162 件②: 已结案卡补充审计链路(唯一渲染 audit_derivation.render_supplementary_chain)
        from tools.aipos_cli.audit_derivation import render_supplementary_chain

        out.append(render_supplementary_chain(str(report.get("task_id") or ""), report["supplementary_audit"]))
    return "\n".join(out + summary)


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
        governance_root = Path(_find_repo_root_for_args(args))  # AIPOS-F144: 唯一薄壳(全局/子命令级 --workspace-root 同义)
        # AIPOS-F133 件②: --lane 校验(不在声明 = 拒, 退出码读 verbs.schema lane_view.invalid_lane_exit_code)
        try:
            lane = resolve_lane_filter(governance_root, getattr(args, "lane", None))
        except LaneFilterInvalid as exc:
            print(f"lybra loop status: {exc}", file=sys.stderr)
            return int(lane_view_declaration()["invalid_lane_exit_code"])
        contract = load_loop_contract()
        # AIPOS-F138 件③ → AIPOS-F142 件②: 信封判定诊断行([ENVELOPE_TRACE])的开关只在发射处一处(autonomy_policy.trace_envelope ←
        # CLI 入口 decide_cli_envelope_trace, --verbose 开), 本处不再另设
        if wait is None:
            report = loop_status(governance_root, task_id, contract=contract, lane=lane,
                                 include_frozen=bool(getattr(args, "include_frozen", False)))
        else:
            report = wait_for_next_action(governance_root, task_id or "", float(wait), contract=contract, lane=lane)
    except StatusUsageError as exc:
        print(f"lybra loop status: {exc}", file=sys.stderr)
        return declared_exit_code(STATUS_VERB, "usage")
    except (FileNotFoundError, LoopRunRecordError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra loop status: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(STATUS_VERB, "unreadable")
    if task_id:  # AIPOS-F226 件②: 本卡晚于最近运行的收件箱裁决(看板 / CLI 同读; 返工上限 / needs_owner 等非 loop 停等事项)= 附裁决行与下一条
        from tools.aipos_cli.frontmatter import FrontmatterReadError
        from tools.aipos_cli.owner_inbox import pending_ruling_lines

        try:
            report["owner_ruling_pending"] = pending_ruling_lines(governance_root, task_id, report)
        except (FrontmatterReadError, OSError, ValueError) as exc:
            print(f"lybra loop status: 收件箱裁决读不出: {type(exc).__name__}: {exc}", file=sys.stderr)
            return declared_exit_code(STATUS_VERB, "unreadable")
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_status(report, run_record_declaration(contract)["states"]))
        for line in report.get("owner_ruling_pending") or []:
            print(line)
        # AIPOS-F167 件③: 末尾「Lybra 未读更新」(四出口同一渲染; 无未读 = 不显示; 只影响显示, 不影响状态判定与退出码)
        from tools.aipos_cli.advisor_updates_cli import advisor_banner_lines

        for line in advisor_banner_lines(governance_root, getattr(args, "actor", None)):
            print(line)
    if task_id and not report["runs"]:
        return declared_exit_code(STATUS_VERB, "no_run")
    if wait is not None and report.get("deploy_changed"):  # AIPOS-F152 件①: 等待中部署已更新 = 声明出口 deploy_changed
        return declared_exit_code(STATUS_VERB, "deploy_changed")
    if wait is not None:  # AIPOS-F136 件①: --wait 退出码按顾问下一动作(verbs.schema lybra_loop_status.exit_codes 同名出口)
        return declared_exit_code(STATUS_VERB, str(report["next_action"]["action"]))
    return declared_exit_code(STATUS_VERB, "ok")


# ---------------------------------------------------------------------------
# AIPOS-F168 件③: `lybra loop note` —— 给本卡最近一次运行补写 Owner 待决结构字段 owner_ask(收件箱据此展示)
# ---------------------------------------------------------------------------

NOTE_VERB = "lybra_loop_note"


def note_declaration() -> dict[str, Any]:
    """verbs.schema lybra_loop_note(allowed_states / texts / exit_codes)。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError, code_repo_schema_root, load_schema

    decl = (load_schema("verbs", code_repo_schema_root()).get("verbs") or {}).get(NOTE_VERB)
    if not isinstance(decl, dict) or any(k not in decl for k in ("allowed_states", "texts", "exit_codes")):
        raise SchemaLoadError(f"verbs.schema.json verbs.{NOTE_VERB} 未声明齐 allowed_states / texts / exit_codes")
    return decl


class LoopNoteRejected(ValueError):
    """loop note 拒写(出口名 = outcome: usage / no_run / run_active)。"""

    def __init__(self, outcome: str, message: str) -> None:
        super().__init__(message)
        self.outcome = outcome


def parse_note_options(raw: list[str] | None) -> list[dict[str, str]]:
    """`键=标签` 列表 → [{key, label}]; 键式样读 lybra_owner_inbox.custom_option.key_pattern(收件箱 / 裁决同一校验)。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration

    pattern = re.compile(str(inbox_declaration()["custom_option"]["key_pattern"]))
    out: list[dict[str, str]] = []
    for text in raw or []:
        key, sep, label = str(text).partition("=")
        key, label = key.strip(), label.strip()
        if not sep or not label or not pattern.fullmatch(key):
            raise LoopNoteRejected("usage", f"选项 {text!r} 须为 键=标签, 键合 {pattern.pattern}")
        if any(o["key"] == key for o in out):
            raise LoopNoteRejected("usage", f"选项键 {key!r} 重复")
        out.append({"key": key, "label": label})
    return out


def render_owner_ask(ask: dict[str, Any]) -> list[str]:
    """运行记录 owner_ask 的人读行(loop status / loop note 同一渲染, 文案读 lybra_loop_note.texts)。"""
    texts = note_declaration()["texts"]
    lines = [str(texts["ask"]).format(noted_by=ask.get("noted_by") or "?", noted_at=ask.get("noted_at") or "?", ask=ask.get("ask") or "")]
    if ask.get("options"):
        lines.append(str(texts["options"]).format(options=" / ".join(f"{o.get('key')}={o.get('label')}" for o in ask["options"])))
    if ask.get("recommend"):
        lines.append(str(texts["recommend"]).format(recommend=ask["recommend"]))
    if ask.get("owner_reason"):  # AIPOS-F225 件③: 第一档事由
        from tools.aipos_cli.owner_inbox import owner_reason_entry

        entry = owner_reason_entry(ask["owner_reason"]) or {"key": ask["owner_reason"], "label": "?"}
        lines.append(str(texts["owner_reason"]).format(**entry))
    if isinstance(ask.get("grant"), dict) and ask["grant"]:  # AIPOS-F180: structured authorization suggestion (advisor fields)
        fields = " · ".join(f"{k}={', '.join(v) if isinstance(v, list) else v}" for k, v in ask["grant"].items())
        lines.append(str(texts["grant"]).format(fields=fields))
    return lines


def render_retracted_ask(ask: dict[str, Any]) -> str:
    """AIPOS-F232 件①: 已撤回请求的人读行(loop status / loop note --retract 同一渲染, 文案读 lybra_loop_note.texts)。"""
    texts = note_declaration()["texts"]
    reason = str(ask.get("retract_reason") or "").strip()
    return str(texts["retracted_ask"]).format(retracted_by=ask.get("retracted_by") or "?", retracted_at=ask.get("retracted_at") or "?",
                                              reason_part=str(texts["retract_reason_part"]).format(reason=reason) if reason else "",
                                              noted_by=ask.get("noted_by") or "?", noted_at=ask.get("noted_at") or "?", ask=ask.get("ask") or "")


def annotate_owner_ask(governance_root: Path, task_id: str, *, ask: str, options: list[str] | None = None,
                       recommend: str | None = None, actor: str | None = None, confirm: bool = False,
                       now: datetime | None = None, grant: dict[str, Any] | None = None,
                       owner_reason: str | None = None) -> dict[str, Any]:
    """件③ 唯一实现: 本卡最近一次运行(与 loop status --task-id 同一取法)state ∈ allowed_states 才写; 预演零写入, 确认 = 读回整份记录
    → 只加 owner_ask → record_writer.render_markdown 原子替换(其余字段与正文原样)→ 经 land_written_paths(loop_note)落账。
    拒 = LoopNoteRejected(outcome)。返回 {stage, record_path, owner_ask, state, landing}。
    AIPOS-F180: grant = 顾问 --grant-* 原值 {task_mode, repos, max_tasks, expires, reason}(经 owner_inbox.normalize_grant_fields 唯一校验)
    → owner_ask.grant(只含给出的字段); 给了 grant 时 ask 可省(缺省 texts.grant_default_ask)。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.record_writer import render_markdown

    from tools.aipos_cli.owner_inbox import normalize_grant_fields

    try:
        grant_fields = normalize_grant_fields(**(grant or {}), now=now)
    except ValueError as exc:
        raise LoopNoteRejected("usage", str(exc)) from exc
    decl = note_declaration()
    asked = bool(str(ask or "").strip())
    if not asked:
        if not grant_fields:
            raise LoopNoteRejected("usage", "--ask 须为非空的一句话(要 Owner 定什么), 或给 --grant-* 授权建议")
        ask = str(decl["texts"]["grant_default_ask"])
    parsed = parse_note_options(options)
    rdecl = run_record_declaration(load_loop_contract())
    root = Path(governance_root)
    files = _run_files(loop_runs_dir(root, task_id))
    if not files:
        raise LoopNoteRejected("no_run", f"卡 {task_id} 无 loop 运行记录(落点 {loop_runs_root(root)})")
    try:
        metas = sorted((read_run(p, rdecl) for p in files), key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
    except LoopRunRecordError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出: {exc}") from exc
    meta = metas[-1]
    state = judge_run(meta, rdecl, now=now)["state"]
    if state not in decl["allowed_states"]:
        raise LoopNoteRejected("run_active", f"卡 {task_id} 最近一次运行 {meta.get('run_id')} 状态 {state}(须 ∈ {decl['allowed_states']}): "
                                             "运行中的记录由 loop 进程原子重写, 补写会被覆盖; 等运行结束再补写")
    path = Path(meta["_path"])
    owner_ask = {"ask": str(ask).strip(), "options": parsed, "recommend": str(recommend or "").strip() or None,
                 "noted_by": str(actor or meta.get("driver") or "").strip() or None, "noted_at": iso_z(now or utc_now())}
    if grant_fields:
        owner_ask["grant"] = grant_fields
    destination = _note_destination(root, task_id, meta, owner_ask, decl)  # AIPOS-F222 件②: 不进收件箱 = 拒写(LoopNoteRejected not_listed)
    # AIPOS-F225 件③: 升 Owner 必带第一档事由(用法 / 运行状态 / 去向都核过之后判; 缺或不在清单 = 拒写 owner_reason_refused)
    owner_ask["owner_reason"] = _note_owner_reason(owner_reason, grant_only=not asked, task_id=task_id, decl=decl)
    report: dict[str, Any] = {"stage": "confirm" if confirm else "preview", "record_path": str(path), "run_id": meta.get("run_id"),
                              "state": state, "owner_ask": owner_ask, "landing": None, "inbox": destination}
    if not confirm:
        return report
    try:
        fm, body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出: {exc}") from exc
    fm["owner_ask"] = owner_ask
    text = render_markdown(fm, body.lstrip("\n"), list(fm))
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    from tools.aipos_cli.governance_commit import land_written_paths
    from tools.schema_loader import SchemaLoadError

    landed_by = owner_ask["noted_by"] or "advisor"
    try:
        report["landing"] = land_written_paths(root, [path.resolve().relative_to(root.resolve()).as_posix()], landed_by,
                                               command="loop_note", task_id=task_id)
    except SchemaLoadError as exc:
        report["landing"] = {"landed": False, "exit_code": 1, "reason": str(exc), "line": f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): {exc}"}
    report["inbox"] = _note_inbox_check(root, destination)
    return report


def _note_owner_reason(value: Any, *, grant_only: bool, task_id: str, decl: dict[str, Any]) -> str:
    """AIPOS-F225 件③: 升 Owner 的第一档事由(校验唯一实现 owner_inbox.require_owner_reason); 缺 / 不在清单 = LoopNoteRejected(owner_reason_refused,
    拒因 texts.owner_reason_refused 指回顾问自处理)。只给 --grant-* = 缺省 tiers.grant_owner_reason。"""
    from tools.aipos_cli.owner_inbox import OwnerReasonRefused, owner_reason_keys, require_owner_reason

    try:
        return require_owner_reason(value, grant=grant_only)
    except OwnerReasonRefused as exc:
        texts = decl["texts"]
        got = str(texts["owner_reason_got"]).format(value=str(exc)) if str(exc) else ""
        raise LoopNoteRejected("owner_reason_refused", str(texts["owner_reason_refused"]).format(
            reasons=owner_reason_keys(), got_part=got, task_id=task_id)) from exc


def retract_owner_ask(governance_root: Path, task_id: str, *, actor: str | None = None, reason: str | None = None,
                      confirm: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """AIPOS-F232 件①(声明 lybra_loop_note --retract) 唯一实现: 顾问撤回本人补写的 Owner 请求。运行取法与 annotate_owner_ask 同一(本卡最近一次运行,
    state ∈ allowed_states); 拒(LoopNoteRejected retract_refused, 原因 texts.retract_refused_reasons): 无 owner_ask / 已撤回 / --actor(缺省
    运行记录 driver)≠ owner_ask.noted_by / 该事项已有 Owner 回应(owner_inbox.owner_ask_destination 同一判定 reason=ruled)。预演零写入;
    确认 = owner_ask 原样留档 + 追加 retracted_at / retracted_by / retract_reason → render_markdown 原子替换 → land_written_paths(loop_note)落账,
    再经 collect_project_inbox 核对该事项已不再列出。返回 {stage, record_path, run_id, state, owner_ask, item_id, landing, inbox}。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError, require_frontmatter
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.owner_inbox import owner_ask_destination
    from tools.aipos_cli.record_writer import render_markdown

    decl = note_declaration()
    texts = decl["texts"]
    rdecl = run_record_declaration(load_loop_contract())
    root = Path(governance_root)

    def _refuse(key: str, **values: Any) -> LoopNoteRejected:
        why = str(texts["retract_refused_reasons"][key]).format(task_id=task_id, **values)
        return LoopNoteRejected("retract_refused", str(texts["retract_refused"]).format(reason=why))

    files = _run_files(loop_runs_dir(root, task_id))
    if not files:
        raise LoopNoteRejected("no_run", f"卡 {task_id} 无 loop 运行记录(落点 {loop_runs_root(root)})")
    try:
        metas = sorted((read_run(p, rdecl) for p in files), key=lambda m: (str(m.get("started_at") or ""), str(m.get("run_id") or "")))
    except LoopRunRecordError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出: {exc}") from exc
    meta = metas[-1]
    run_id = str(meta.get("run_id") or "?")
    state = judge_run(meta, rdecl, now=now)["state"]
    if state not in decl["allowed_states"]:
        raise LoopNoteRejected("run_active", f"卡 {task_id} 最近一次运行 {run_id} 状态 {state}(须 ∈ {decl['allowed_states']}): "
                                             "运行中的记录由 loop 进程原子重写, 撤回会被覆盖; 等运行结束再撤回")
    ask = meta.get("owner_ask") if isinstance(meta.get("owner_ask"), dict) else {}
    if not str(ask.get("ask") or "").strip():
        raise _refuse("no_ask", run_id=run_id)
    if ask.get("retracted_at"):
        raise _refuse("already", run_id=run_id, retracted_at=ask.get("retracted_at"), retracted_by=ask.get("retracted_by") or "?")
    who = str(actor or meta.get("driver") or "").strip()
    noted_by = str(ask.get("noted_by") or "").strip()
    if noted_by and who != noted_by:
        raise _refuse("not_own", run_id=run_id, noted_by=noted_by, actor=who or "(空)")
    try:
        runs = loop_status(root, task_id).get("runs") or []
    except LoopRunRecordError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出(收件箱去向判不出, 不写): {exc}") from exc
    view = runs[-1] if runs else {}
    na = dict(view.get("next_action") or {})
    if na.get("reason") == "owner_ruled" and isinstance(na.get("ruled_from"), dict):
        na = {**na, "action": na["ruled_from"].get("action") or "owner_needed", "reason": na["ruled_from"].get("reason")}
    dest = owner_ask_destination(root, task_id, {**view, "owner_ask": ask}, na) or {}
    if dest.get("reason") == "ruled":
        raise _refuse("ruled", item_id=dest.get("item_id") or "?", decision_id=dest.get("decision_id") or "?", choice=dest.get("choice") or "?")
    retracted = {**ask, "retracted_at": iso_z(now or utc_now()), "retracted_by": who or None,
                 "retract_reason": str(reason or "").strip() or None}
    path = Path(meta["_path"])
    report: dict[str, Any] = {"stage": "confirm" if confirm else "preview", "record_path": str(path), "run_id": run_id, "state": state,
                              "owner_ask": retracted, "item_id": dest.get("item_id"), "landing": None, "inbox": None}
    if not confirm:
        return report
    try:
        fm, body = require_frontmatter(path)
    except FrontmatterReadError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出: {exc}") from exc
    fm["owner_ask"] = retracted
    text = render_markdown(fm, body.lstrip("\n"), list(fm))
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    from tools.aipos_cli.governance_commit import land_written_paths
    from tools.schema_loader import SchemaLoadError

    try:
        report["landing"] = land_written_paths(root, [path.resolve().relative_to(root.resolve()).as_posix()], who or "advisor",
                                               command="loop_note", task_id=task_id)
    except SchemaLoadError as exc:
        report["landing"] = {"landed": False, "exit_code": 1, "reason": str(exc), "line": f"⚠ 落账未完成(写入已生效, 勿重复执行写命令): {exc}"}
    if dest.get("item_id") and dest.get("merged"):  # 并入的返工上限事项本身仍待定(与本请求无关), 不按「已不再列出」核
        report["inbox"] = {"item_id": dest["item_id"], "merged": True, "listed": None, "project": None, "errors": []}
    elif dest.get("item_id"):
        from tools.aipos_cli.owner_inbox import collect_project_inbox

        inbox = collect_project_inbox(root, categories=("loop_owner_needed", "rework_limit"))
        listed = any(i["item_id"] == dest["item_id"] for i in [*inbox["items"], *(inbox.get("notices") or [])])
        report["inbox"] = {"item_id": dest["item_id"], "listed": listed, "project": inbox["project"],
                           "errors": [f"{e['source']}: {e['error']}" for e in inbox["errors"]]}
    return report


def _note_destination(root: Path, task_id: str, meta: dict[str, Any], owner_ask: dict[str, Any], decl: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F222 件②: 本次 owner_ask 进哪个收件箱事项(收件箱同一判定 owner_inbox.owner_ask_destination; 运行视图与下一动作取 loop status
    同一推导, 已裁决改写的下一动作还原为原判)。不会进收件箱 = LoopNoteRejected(not_listed, 原因 + 指路)。返回 {item_id, category,
    category_label, merged}。"""
    from tools.aipos_cli.owner_inbox import inbox_declaration, owner_ask_destination

    texts = decl["texts"]
    reasons = texts["not_listed_reasons"]
    try:
        runs = loop_status(root, task_id).get("runs") or []
    except LoopRunRecordError as exc:
        raise LoopNoteRejected("no_run", f"运行记录读不出(收件箱去向判不出, 不写): {exc}") from exc
    view = runs[-1] if runs else {}

    def _reject(key: str, **values: Any) -> LoopNoteRejected:
        reason = str(reasons[key]).format(task_id=task_id, **values)
        return LoopNoteRejected("not_listed", str(texts["not_listed"]).format(reason=reason, hint=str(texts["not_listed_hint"]).format(
            task_id=task_id)))

    if str(view.get("run_id") or "") != str(meta.get("run_id") or ""):
        raise _reject("not_latest", run_id=meta.get("run_id") or "?", latest_run_id=view.get("run_id") or "?")
    na = dict(view.get("next_action") or {})
    if na.get("reason") == "owner_ruled" and isinstance(na.get("ruled_from"), dict):
        na = {**na, "action": na["ruled_from"].get("action") or "owner_needed", "reason": na["ruled_from"].get("reason")}
    dest = owner_ask_destination(root, task_id, {**view, "owner_ask": owner_ask}, na)
    if dest is None:  # owner_ask 已校验非空, 不会到这里; 防御: 不猜
        raise LoopNoteRejected("usage", "--ask 为空, 无可进收件箱的请求")
    if dest["reason"] == "card_closed":
        raise _reject("card_closed", state=dest.get("state") or "?")
    if dest["reason"] == "frozen":
        raise _reject("frozen")
    if dest["reason"] == "ruled":
        raise _reject("ruled", item_id=dest["item_id"], decision_id=dest.get("decision_id") or "?", choice=dest.get("choice") or "?")
    label = str(inbox_declaration()["categories"][dest["category"]]["label"])
    return {"item_id": dest["item_id"], "category": dest["category"], "category_label": label, "merged": bool(dest["merged"])}


def _note_inbox_check(root: Path, destination: dict[str, Any]) -> dict[str, Any]:
    """AIPOS-F222 件②: 写入后经收件箱同一收集(owner_inbox.collect_project_inbox, 只算 loop / 返工上限两类)核对该事项已列出。"""
    from tools.aipos_cli.owner_inbox import collect_project_inbox

    inbox = collect_project_inbox(root, categories=("loop_owner_needed", "rework_limit"))
    listed = any(item["item_id"] == destination["item_id"] for item in inbox["items"])
    return {**destination, "listed": listed, "errors": [f"{e['source']}: {e['error']}" for e in inbox["errors"]],
            "project": inbox["project"]}


def _inbox_category_label(category: Any) -> str:
    from tools.aipos_cli.owner_inbox import inbox_declaration

    return str(((inbox_declaration()["categories"].get(str(category or "")) or {}).get("label")) or category or "?")


def _note_inbox_line(inbox: dict[str, Any], texts: dict[str, Any], *, confirmed: bool) -> str:
    """loop note 回显「进哪个收件箱事项」(texts.inbox_will / inbox_listed / inbox_missing)。"""
    values = {"item_id": inbox.get("item_id") or "?", "category_label": inbox.get("category_label") or "?",
              "merged_part": str(texts["merged_part"]) if inbox.get("merged") else ""}
    if not confirmed:
        return str(texts["inbox_will"]).format(**values)
    if inbox.get("listed") and inbox.get("legacy") and texts.get("inbox_legacy"):  # AIPOS-F232 件①: 旧请求降为第二档告知
        return str(texts["inbox_legacy"]).format(**values)
    if inbox.get("listed"):
        return str(texts["inbox_listed"]).format(**values)
    return str(texts["inbox_missing"]).format(item_id=values["item_id"], errors="; ".join(inbox.get("errors") or []) or "无",
                                              project=inbox.get("project") or "?")


def loop_note_cli(args: Any) -> int:
    """`lybra loop note` 薄壳: 全部逻辑在 annotate_owner_ask; 退出码读 verbs.schema lybra_loop_note.exit_codes(落账失败读 post_write_landing)。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    task_id = getattr(args, "task_id", None)
    if not task_id:
        print("lybra loop note: 须给 --task-id", file=sys.stderr)
        return declared_exit_code(NOTE_VERB, "usage")
    if getattr(args, "retract", False):  # AIPOS-F232 件①: 撤回本人请求(与写请求的参数互斥)
        return _loop_note_retract_cli(args, task_id)
    try:
        root = Path(_find_repo_root_for_args(args))
        grant = {"task_mode": getattr(args, "grant_mode", None), "repos": getattr(args, "grant_repos", None),
                 "max_tasks": getattr(args, "grant_max_tasks", None), "expires": getattr(args, "grant_expires", None),
                 "reason": getattr(args, "grant_reason", None)}
        report = annotate_owner_ask(root, task_id, ask=getattr(args, "ask", None) or "", options=getattr(args, "note_options", None),
                                    recommend=getattr(args, "recommend", None), actor=getattr(args, "actor", None),
                                    confirm=bool(getattr(args, "confirm", False)), grant=grant,
                                    owner_reason=getattr(args, "owner_reason", None))
    except LoopNoteRejected as exc:
        print(f"lybra loop note: {exc}", file=sys.stderr)
        return declared_exit_code(NOTE_VERB, exc.outcome)
    except (FileNotFoundError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra loop note: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(NOTE_VERB, "no_run")
    landing = report.get("landing")
    if getattr(args, "json", False):
        print(json.dumps({k: v for k, v in report.items() if k != "landing"} | {"landing": {k: v for k, v in (landing or {}).items() if k != "line"} or None},
                         indent=2, ensure_ascii=False))
    else:
        texts = note_declaration()["texts"]
        print(str(texts["preview" if report["stage"] == "preview" else "written"]).format(record_path=report["record_path"],
                                                                                          run_id=report["run_id"], state=report["state"]))
        print("\n".join(render_owner_ask(report["owner_ask"])))
        print(_note_inbox_line(report.get("inbox") or {}, texts, confirmed=report["stage"] != "preview"))
        if landing:
            print(landing["line"], file=sys.stdout if landing.get("landed") else sys.stderr)
    if landing and not landing.get("landed"):
        return int(landing.get("exit_code") or 1)
    return declared_exit_code(NOTE_VERB, "ok")


def _loop_note_retract_cli(args: Any, task_id: str) -> int:
    """`lybra loop note --retract` 薄壳(AIPOS-F232 件①): 全部逻辑在 retract_owner_ask; 与 --ask / --options / --recommend / --owner-reason /
    --grant-* 同给 = 拒(retract_refused); 退出码读 verbs.schema lybra_loop_note.exit_codes。"""
    import json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args
    from tools.aipos_cli.verb_contract import declared_exit_code
    from tools.schema_loader import SchemaLoadError

    texts = note_declaration()["texts"]
    mixed = [k for k in ("ask", "note_options", "recommend", "owner_reason", "grant_mode", "grant_repos", "grant_max_tasks", "grant_expires",
                         "grant_reason") if getattr(args, k, None)]
    if mixed:
        print("lybra loop note: " + str(texts["retract_refused"]).format(reason=texts["retract_refused_reasons"]["with_ask"]), file=sys.stderr)
        return declared_exit_code(NOTE_VERB, "retract_refused")
    try:
        root = Path(_find_repo_root_for_args(args))
        report = retract_owner_ask(root, task_id, actor=getattr(args, "actor", None), reason=getattr(args, "retract_reason", None),
                                   confirm=bool(getattr(args, "confirm", False)))
    except LoopNoteRejected as exc:
        print(f"lybra loop note: {exc}", file=sys.stderr)
        return declared_exit_code(NOTE_VERB, exc.outcome)
    except (FileNotFoundError, SchemaLoadError, ValueError, OSError) as exc:
        print(f"lybra loop note: {type(exc).__name__}: {exc}", file=sys.stderr)
        return declared_exit_code(NOTE_VERB, "no_run")
    landing = report.get("landing")
    if getattr(args, "json", False):
        print(json.dumps({k: v for k, v in report.items() if k != "landing"} | {"landing": {k: v for k, v in (landing or {}).items() if k != "line"} or None},
                         indent=2, ensure_ascii=False))
    else:
        head = "retract_preview" if report["stage"] == "preview" else "retract_written"
        print(str(texts[head]).format(record_path=report["record_path"], run_id=report["run_id"], state=report["state"]))
        print(render_retracted_ask(report["owner_ask"]))
        inbox = report.get("inbox")
        if inbox and inbox.get("merged"):
            print(str(texts["retract_merged"]).format(item_id=inbox["item_id"]))
        elif inbox:
            print(str(texts["retract_still_listed" if inbox["listed"] else "retract_removed"]).format(
                item_id=inbox["item_id"], errors="; ".join(inbox.get("errors") or []) or "无", project=inbox.get("project") or "?"))
        if landing:
            print(landing["line"], file=sys.stdout if landing.get("landed") else sys.stderr)
    if landing and not landing.get("landed"):
        return int(landing.get("exit_code") or 1)
    return declared_exit_code(NOTE_VERB, "ok")


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation  # noqa: E402
check_direct_invocation(__name__)
