#!/usr/bin/env python3
"""AIPOS-F95 夹具: 假 harness(替身 `pi -p {kickoff} --mode json`; 禁起真实 pi 会话)。

调用: `python3 tests/fake_harness.py <kickoff>`(cwd = 工位目录, 由 lybra loop 按测试声明的 launch 模板拉起)。
行为由环境变量选择(按卡角色分开: 卡号以 R / R<轮次> 结尾 = 审计卡, AIPOS-F112 复审轮 R2/R3… 同):
  FAKE_EXEC_MODE / FAKE_AUDIT_MODE ∈
    work    吐 pi-json 事件 → 按 kickoff 干活(执行卡: 工作树提交 + Return; 审计卡: 审计报告)→ 写 pi 会话记录 → 退出 0
    linger  同 work 但写完产物不退出(验产物就绪后宽限期满终止进程组)
    hang    吐事件后挂起, 另起一个孙进程同挂(验超时/信号杀整组); 忽略 SIGTERM(验 SIGKILL 升级)
    early   往 stderr 写几行后 exit 7(验早退 exit 3 附 stderr 末尾)
    stop    AIPOS-F171B: 只发一条纯文字(「let me poll」)即 exit 0、不写产物(半途停笔; 验同会话续接用尽 → 早退)
    stop_once  AIPOS-F171B: 首次(FAKE_PI_RESUMES=0)同 stop, 同会话续接时(FAKE_PI_RESUMES≥1, 假 pi 设)同 work
    sha_typo_once  AIPOS-F180B (audit card only): first run writes the report with commit_sha missing 3 middle chars (the
                   F180R shape); on a same-session resume it rewrites only the header commit_sha to the tip
    sha_typo   AIPOS-F180B: first run as sha_typo_once; on resume rewrites the header with another wrong value (tip[:38])
    sha_stay   AIPOS-F180B: first run as sha_typo_once; on resume leaves the report untouched and exits 0
    sha_other  AIPOS-F180B: report commit_sha = FAKE_AUDIT_OTHER_SHA (another real commit = audited the wrong product)
    validate   AIPOS-F196 (executor card only): fixed-candidate validation card (no worktree): run the kickoff's validation-root
               ssh commands (directory-chain init-run, then helper check of the screenshot path), write a fake screenshot into the
               run directory over `ssh <alias>`, copy it to the report slot artifacts/, write RETURN with the product-given values
               (AIPOS-F197: between init-run and the screenshot, deliver the candidate source with the kickoff's command and run the
               kickoff's src-verify; both outputs go to artifacts/commands.txt)
    validate_outputs_once  AIPOS-F204 (executor card only): first run as validate, but the RETURN frontmatter is the LANTU-VALID1
               shape (outputs list + candidate / tree keys, no commit_sha / tree_hash / branch); on a same-session resume it copies the
               product values out of the resume instruction (FAKE_PI_PROMPT, `key = value(`) and rewrites only the frontmatter
    validate_outputs       AIPOS-F204: first run as validate_outputs_once; on resume rewrites the frontmatter still wrong (adds a key)
    docs       AIPOS-F197 (executor card only): governance-root-only card (no worktree): write a doc into the report slot artifacts/
               named by the kickoff's「产物落点」and a RETURN whose frontmatter `outputs` lists it (no branch keys)
    hang_sim   AIPOS-F231: 同 hang, 另起一个脱离本进程组(新会话)的「伪模拟器」进程, cwd = FAKE_SIM_DIR(本卡验证根 run 目录; 验停线按
               run 目录归属清理组外进程)
    provider_401 / provider_429 / length   AIPOS-F231: 写一份 pi 会话记录(文件名 <时刻>_<FAKE_PI_SESSION_ID>.jsonl, 与真实 pi 同形)
               末条助手消息 stopReason=error + errorMessage「401: …」/「429: …」, 或 stopReason=length(本轮输入 = usage.input +
               cacheRead), 不写产物即 exit 0(验早退真实原因 / 退避重试 / 超窗口不续接)
    provider_429_once   AIPOS-F231: 首次同 provider_429, 同会话续接时(FAKE_PI_RESUMES≥1)同 work(验退避重试后完成)
  审计卡: 开工提示给出 `- outputs_digest: <值>(` (AIPOS-F197 被审卡只写治理根)= 报告写 verdict + outputs_digest(照抄), 否则写被审 tip
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


def provider_session(mode: str) -> None:
    """AIPOS-F231: 模拟 pi 早退的会话记录(真实形: ~/.pi/agent/sessions/--<cwd>--/<时刻>_<会话 id>.jsonl)。"""
    sid = os.environ.get("FAKE_PI_SESSION_ID") or "no-session"
    cwd = os.getcwd()
    path = Path(os.environ["HOME"]) / ".pi" / "agent" / "sessions" / ("--" + cwd.strip("/").replace("/", "-") + "--") / f"2026-10-25T00-00-00-000Z_{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "length":
        last = {"role": "assistant", "provider": "kiwiai", "model": "qwen3.7-plus", "stopReason": "length", "errorMessage": None,
                "usage": {"input": 4725, "output": 1, "cacheRead": 93568}, "content": [{"type": "thinking", "thinking": ""}]}
    else:
        code = "401" if mode == "provider_401" else "429"
        text = ("invalid access token or token expired" if code == "401" else "usage allocated quota exceeded. please try again later")
        last = {"role": "assistant", "provider": "kiwiai", "model": "qwen3.7-plus", "stopReason": "error",
                "errorMessage": f'{code}: {{"message":"litellm.BadRequestError: OpenAIException - {text}","code":"{code}"}}',
                "usage": {"input": 0, "output": 0, "cacheRead": 0}, "content": []}
    lines = [{"type": "session", "version": 3, "id": sid, "cwd": cwd},
             {"type": "model_change", "provider": "kiwiai", "modelId": "qwen3.7-plus"},
             {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "kickoff"}]}},
             {"type": "message", "message": last}]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(x) for x in lines) + "\n")


def reviewed_of(task_id: str) -> str | None:
    """审计轮卡号(<被审卡>R / <被审卡>R2 …, 默认声明序列)→ 被审卡号; 非审计卡 = None。"""
    match = re.match(r"^(.+?)R(\d*)$", task_id, re.I)
    return match.group(1) if match and match.group(2) != "1" else None


def do_work(task_id: str, kickoff: str, sha: str | None = None) -> None:
    report = Path(field(kickoff, "报告落点"))
    digest = re.search(r"^- outputs_digest: ([0-9a-f]{64})\(", kickoff, re.M)
    if reviewed_of(task_id) and digest:
        # AIPOS-F197: 被审卡只写治理根 = 报告绑产品给出的产出清单指纹(照抄)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(frontmatter({"task_id": task_id, "reviewed_task_id": reviewed_of(task_id), "verdict": "PASS",
                                       "outputs_digest": digest.group(1)},
                                      "# 审计报告\n\n## 一句话结论\n产出清单逐项复核通过(假 harness)。\n"), encoding="utf-8")
        pi_session(report, "fake-audit-model")
        emit({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "write", "isError": False, "result": {"content": []}})
        return
    if reviewed_of(task_id):
        tip = re.search(r"^- commit_sha: ([0-9a-f]{40})\(", kickoff, re.M)
        if not tip:
            raise SystemExit("fake_harness: 审计 kickoff 缺被审 tip 实值")
        emit({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "read", "args": {"path": field(kickoff, "任务卡路径")}})
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(frontmatter({"task_id": task_id, "reviewed_task_id": reviewed_of(task_id), "verdict": "PASS",
                                       "commit_sha": sha or tip.group(1)},
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


def do_validate(task_id: str, kickoff: str) -> None:
    """AIPOS-F196: validation card (fixed candidate, no worktree) driven only by the product kickoff text."""
    report = Path(field(kickoff, "报告落点"))
    values = dict(re.findall(r"^- (commit_sha|tree_hash|branch): (\S+?)\(", kickoff, re.M))
    alias = re.search(r"ssh 别名 = (\S+?), ", kickoff).group(1)
    run_dir = re.search(r"本次只写 `([^`]+)`", kickoff).group(1)
    init = re.search(r"在治理根所在机运行 `([^`]+)`\(helper 由产品给出", kickoff).group(1)
    check = re.search(r"每次写前在治理根所在机运行 `([^`]+)`", kickoff).group(1)
    log = []
    proc = subprocess.run(["bash", "-c", init], capture_output=True, text=True, timeout=120)
    log.append(f"$ {init}\nexit {proc.returncode}\n{proc.stdout.strip()}{proc.stderr.strip()}")
    if proc.returncode != 0 or not proc.stdout.strip().splitlines()[-1].startswith("INIT_OK"):
        raise SystemExit("fake_harness: init-run failed:\n" + log[-1])
    # AIPOS-F197 件④: 候选源码送达 + 目标主机核树指纹(命令只取自开工提示; 本机形 / ssh 形同一取法)
    src_line = next((ln for ln in kickoff.splitlines() if "② 候选源码送达" in ln), "")
    if src_line:
        deliver = (re.search(r"运行 `([^`]+)`\(只把已提交", src_line) or re.search(r"解到 `[^`]+`: `([^`]+)`; 再核对", src_line)).group(1)
        verify = re.search(r"\): `([^`]+)`; 只认末行 `SRC_OK`", src_line).group(1)
        for label, command in (("deliver", deliver), ("verify", verify)):
            proc = subprocess.run(["bash", "-o", "pipefail", "-c", command], capture_output=True, text=True, timeout=120)
            log.append(f"$ {command}\nexit {proc.returncode}\n{proc.stdout.strip()}{proc.stderr.strip()}")
            if proc.returncode != 0 or (label == "verify" and not proc.stdout.strip().splitlines()[-1].startswith("SRC_OK")):
                raise SystemExit(f"fake_harness: source {label} failed:\n" + log[-1])
    target = f"{run_dir}/screenshot.png"
    command = check.replace("<目标>", target)
    proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=120)
    log.append(f"$ {command}\nexit {proc.returncode}\n{proc.stdout.strip()}{proc.stderr.strip()}")
    if proc.returncode != 0 or "ALLOW" not in proc.stdout:
        raise SystemExit("fake_harness: helper check refused:\n" + log[-1])
    write = f"printf 'fake-png-{task_id}' > {target}"
    proc = subprocess.run(["ssh", alias, write], capture_output=True, text=True, timeout=60)
    log.append(f"$ ssh {alias} {write!r}\nexit {proc.returncode}")
    shot = report.parent / "artifacts" / "screenshot.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(Path(target).read_bytes())
    (report.parent / "artifacts" / "commands.txt").write_text("\n\n".join(log) + "\n", encoding="utf-8")
    emit({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "bash", "args": {"command": f"ssh {alias} (validation run)"}})
    report.write_text(frontmatter(values, f"# RETURN — {task_id}\n\n## 一句话结论\n固定候选验证完成(假 harness): 截图见 artifacts/screenshot.png。\n\n"
                                          f"## 证据\n- run 目录 {run_dir}\n- artifacts/commands.txt\n"), encoding="utf-8")
    pi_session(report, "fake-exec-model")
    emit({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "write", "isError": False, "result": {"content": []}})


def validate_outputs_mode(task_id: str, kickoff: str, mode: str) -> None:
    """AIPOS-F204: validation card whose executor writes the governance-root outputs contract instead of the three product keys."""
    report = Path(field(kickoff, "报告落点"))
    if os.environ.get("FAKE_PI_RESUMES", "0") == "0":
        do_validate(task_id, kickoff)
        values = dict(re.findall(r"^- (commit_sha|tree_hash): (\S+?)\(", kickoff, re.M))
        body = report.read_text(encoding="utf-8").split("---\n", 2)[2]
        outputs = sorted(str(p) for p in (report.parent / "artifacts").iterdir())
        report.write_text(f"---\ntask_id: {task_id}\noutputs:\n" + "".join(f"  - {o}\n" for o in outputs)
                          + f"candidate: {values['commit_sha']}\ntree: {values['tree_hash']}\n---\n" + body, encoding="utf-8")
        return
    text = report.read_text(encoding="utf-8")
    body = text.split("---\n", 2)[2]
    if mode == "validate_outputs":
        report.write_text(text.replace("---\n", "---\nretried: yes\n", 1), encoding="utf-8")
    else:
        values = dict(re.findall(r"(commit_sha|tree_hash|branch) = (\S+?)\(", os.environ.get("FAKE_PI_PROMPT", "")))
        if set(values) != {"commit_sha", "tree_hash", "branch"}:
            raise SystemExit(f"fake_harness: resume instruction lacks product values: {os.environ.get('FAKE_PI_PROMPT')!r}")
        report.write_text(frontmatter({"task_id": task_id, **values}, body), encoding="utf-8")
    pi_session(report, "fake-exec-model")


def do_docs(task_id: str, kickoff: str) -> None:
    """AIPOS-F197: governance-root-only card (no worktree) driven only by the product kickoff text (产物落点 + outputs contract)."""
    note = field(kickoff, "产物落点")
    report = Path(field(kickoff, "报告落点"))
    if "- outputs: (" not in kickoff or "commit_sha" in kickoff:
        raise SystemExit("fake_harness: governance-root kickoff 缺 outputs 契约或仍要求分支三键")
    doc = report.parent / "artifacts" / f"{task_id.lower()}-guide.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(f"# {task_id} 文档产物(假 harness)\n\n依据开工提示: {note[:40]}…\n", encoding="utf-8")
    emit({"type": "tool_execution_start", "toolCallId": "t2", "toolName": "write", "args": {"path": str(doc)}})
    report.write_text(f"---\noutputs:\n- {doc}\n---\n# RETURN — {task_id}\n\n## 一句话结论\n文档已写到报告落点 artifacts/(假 harness)。\n",
                      encoding="utf-8")
    pi_session(report, "fake-exec-model")
    emit({"type": "tool_execution_end", "toolCallId": "t2", "toolName": "write", "isError": False, "result": {"content": []}})


def sha_mode(task_id: str, kickoff: str, mode: str) -> None:
    """AIPOS-F180B: report-header commit_sha fixture modes (audit card only); resume = FAKE_PI_RESUMES >= 1 (set by fake pi)."""
    tip = re.search(r"^- commit_sha: ([0-9a-f]{40})\(", kickoff, re.M).group(1)
    report = Path(field(kickoff, "报告落点"))
    if os.environ.get("FAKE_PI_RESUMES", "0") == "0":
        bad = os.environ["FAKE_AUDIT_OTHER_SHA"] if mode == "sha_other" else tip[:20] + tip[23:]
        do_work(task_id, kickoff, sha=bad)
        return
    if mode == "sha_stay":
        return
    fixed = tip if mode == "sha_typo_once" else tip[:38]
    text = report.read_text(encoding="utf-8")
    report.write_text(re.sub(r"^commit_sha: .*$", f"commit_sha: {fixed}", text, count=1, flags=re.M), encoding="utf-8")
    pi_session(report, "fake-audit-model")


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
    if mode == "stop" or (mode == "stop_once" and os.environ.get("FAKE_PI_RESUMES", "0") == "0"):
        emit({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "UI tests still running… let me poll"}]}})
        emit({"type": "agent_end", "messages": []})
        return 0
    if mode in ("hang", "hang_sim"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--grandchild"])
        if mode == "hang_sim":  # AIPOS-F231: 「伪模拟器」脱离本进程组(新会话), 只能按验证根 run 目录(cwd)归属清理
            sim_dir = os.environ["FAKE_SIM_DIR"]
            os.makedirs(sim_dir, exist_ok=True)
            sim = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--grandchild"], cwd=sim_dir, start_new_session=True)
            if log:
                Path(log, f"{task_id}.sim_pid").write_text(str(sim.pid), encoding="utf-8")
        time.sleep(600)
        return 0
    if mode in ("provider_401", "provider_429", "length") or (mode == "provider_429_once" and os.environ.get("FAKE_PI_RESUMES", "0") == "0"):
        provider_session("provider_429" if mode == "provider_429_once" else mode)  # AIPOS-F231: 早退真实原因在会话记录里
        emit({"type": "agent_end", "messages": []})
        return 0
    if mode.startswith("sha_") and reviewed_of(task_id):
        sha_mode(task_id, kickoff, mode)
        emit({"type": "agent_end", "messages": []})
        return 0
    if mode in ("validate_outputs_once", "validate_outputs") and not reviewed_of(task_id):
        validate_outputs_mode(task_id, kickoff, mode)
        emit({"type": "agent_end", "messages": []})
        return 0
    if mode == "validate" and not reviewed_of(task_id):
        do_validate(task_id, kickoff)
        emit({"type": "agent_end", "messages": []})
        return 0
    if mode == "docs" and not reviewed_of(task_id):
        do_docs(task_id, kickoff)
        emit({"type": "agent_end", "messages": []})
        return 0
    do_work(task_id, kickoff)
    emit({"type": "agent_end", "messages": []})
    if mode == "linger":
        time.sleep(600)
    return 0


if __name__ == "__main__":
    sys.exit(main())
