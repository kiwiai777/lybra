#!/usr/bin/env python3
"""AIPOS-F95 夹具: 假 harness(替身 `pi -p {kickoff} --mode json`; 禁起真实 pi 会话)。

调用: `python3 tests/fake_harness.py <kickoff>`(cwd = 工位目录, 由 lybra loop 按测试声明的 launch 模板拉起)。
行为由环境变量选择(按卡角色分开: 卡号以 R / R<轮次> 结尾 = 审计卡, AIPOS-F112 复审轮 R2/R3… 同):
  FAKE_EXEC_MODE / FAKE_AUDIT_MODE ∈
    work    吐 pi-json 事件 → 按 kickoff 干活(执行卡: 工作树提交 + Return; 审计卡: 审计报告)→ 写 pi 会话记录 → 退出 0
    linger  同 work 但写完产物不退出(验产物就绪后宽限期满终止进程组)
    hang    吐事件后挂起, 另起一个孙进程同挂(验超时/信号杀整组); 忽略 SIGTERM(验 SIGKILL 升级)
    early   往 stderr 写几行后 exit 7(验早退 exit 3 附 stderr 末尾)
  FAKE_HARNESS_LOG: 目录; 收到的 argv[1](kickoff)按字节原样落 <卡号>.kickoff, cwd 落 <卡号>.cwd(供逐字节比对)
事件里故意含一行凭据字样、一个未知事件类型、一行非 JSON(验汇总隐去/计数不刷屏)。
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def git(cwd: str, *argv: str) -> str:
    return subprocess.run(["git", "-c", "user.name=fake", "-c", "user.email=fake@fake", *argv], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def frontmatter(meta: dict, body: str) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in meta.items()) + "---\n" + body


def field(kickoff: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}: (.+)$", kickoff, re.M)
    if not match:
        raise SystemExit(f"fake_harness: kickoff 缺「{label}」")
    return match.group(1).strip()


def pi_session(report: Path, model: str) -> None:
    """模拟 pi 会话记录(产品按 card.schema session_record_locators 取运行时模型)。"""
    cwd = os.getcwd()
    path = Path(os.environ["HOME"]) / ".pi" / "agent" / "sessions" / ("--" + cwd.strip("/").replace("/", "-") + "--") / "fake.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session", "version": 3, "id": "fake", "cwd": cwd},
        {"type": "model_change", "provider": "fake", "modelId": model},
        {"type": "message", "message": {"role": "assistant", "provider": "fake", "model": model, "content": [
            {"type": "toolCall", "id": "c1", "name": "write", "arguments": {"path": str(report), "content": "..."}}]}},
    ]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")


def reviewed_of(task_id: str) -> str | None:
    """审计轮卡号(<被审卡>R / <被审卡>R2 …, 默认声明序列)→ 被审卡号; 非审计卡 = None。"""
    match = re.match(r"^(.+?)R(\d*)$", task_id, re.I)
    return match.group(1) if match and match.group(2) != "1" else None


def do_work(task_id: str, kickoff: str) -> None:
    report = Path(field(kickoff, "报告落点"))
    if reviewed_of(task_id):
        tip = re.search(r"^- commit_sha: ([0-9a-f]{40})\(", kickoff, re.M)
        if not tip:
            raise SystemExit("fake_harness: 审计 kickoff 缺被审 tip 实值")
        emit({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "read", "args": {"path": field(kickoff, "任务卡路径")}})
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(frontmatter({"task_id": task_id, "reviewed_task_id": reviewed_of(task_id), "verdict": "PASS",
                                       "commit_sha": tip.group(1)},
                                      "# 审计报告\n\n## 一句话结论\n逐条复核通过(假 harness)。\n\n## 证据\n- 夹具绿\n"), encoding="utf-8")
        pi_session(report, "fake-audit-model")
    else:
        worktree = field(kickoff, "工作树路径")
        rel = f"tests/test_{task_id.lower().replace('-', '_')}.py"
        Path(worktree, rel).parent.mkdir(parents=True, exist_ok=True)
        Path(worktree, rel).write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        emit({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "bash", "args": {"command": f"git -C {worktree} commit -m work\nsecond line"}})
        git(worktree, "add", rel)
        git(worktree, "commit", "-q", "-m", f"{task_id}: work (fake harness)")
        sha, tree = git(worktree, "rev-parse", "HEAD"), git(worktree, "rev-parse", "HEAD^{tree}")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(frontmatter({"commit_sha": sha, "tree_hash": tree, "branch": f"card/{task_id}"},
                                      f"# RETURN — {task_id}\n\n## 一句话结论\n完成(假 harness)。\n\n## 改动清单\n- {rel}\n"), encoding="utf-8")
        pi_session(report, "fake-exec-model")
    emit({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "write", "isError": False, "result": {"content": []}})


def main() -> int:
    if sys.argv[1:2] == ["--grandchild"]:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(600)
        return 0
    kickoff = sys.argv[1]
    task_id = re.search(r"已认领任务卡 (\S+?)。", kickoff).group(1)
    log = os.environ.get("FAKE_HARNESS_LOG")
    if log:
        Path(log).mkdir(parents=True, exist_ok=True)
        Path(log, f"{task_id}.kickoff").write_bytes(sys.argv[1].encode("utf-8"))
        Path(log, f"{task_id}.cwd").write_text(os.getcwd(), encoding="utf-8")
        Path(log, f"{task_id}.pgid").write_text(str(os.getpgid(0)), encoding="utf-8")
    mode = os.environ.get("FAKE_AUDIT_MODE" if reviewed_of(task_id) else "FAKE_EXEC_MODE", "work")

    emit({"type": "session", "version": 3, "id": "fake", "timestamp": "2026-10-04T00:00:00Z", "cwd": os.getcwd()})
    emit({"type": "agent_start"})
    emit({"type": "turn_start"})
    emit({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "read", "args": {"path": "AGENTS.md", "offset": 1}})
    emit({"type": "message_update", "usage": {}, "assistantMessageEvent": {"type": "text_delta", "delta": "x"}})
    emit({"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "text", "text": f"开工 {task_id}: 先读卡面\n第二行不应上屏"}]}})
    emit({"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "the api token is fixture-not-a-secret-value"}]}})
    emit({"type": "weird_future_event", "payload": 1})
    sys.stdout.write("this line is not json\n")
    sys.stdout.flush()
    emit({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "read", "isError": True,
          "result": {"content": [{"type": "text", "text": "ENOENT: no such file\nstack"}]}})

    if mode == "early":
        for i in range(1, 26):
            sys.stderr.write(f"fake stderr line {i}\n")
        sys.stderr.write("fatal: fake harness crashed early\n")
        sys.stderr.flush()
        return 7
    if mode == "hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--grandchild"])
        time.sleep(600)
        return 0
    do_work(task_id, kickoff)
    emit({"type": "agent_end", "messages": []})
    if mode == "linger":
        time.sleep(600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
