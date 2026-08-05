#!/usr/bin/env python3
"""AIPOS-340F6 — turn-advancer ``--mode auto`` 执行出口测试。

覆盖验收断言:
  1. auto 真执行 + 退出码透传(成功 0 / 失败非零透传);
  2. wait_human / "等待人工"占位 / requires_human_judgment 场景 auto 拒绝执行;
  3. 执行前后留痕(append-only,一文件一事件,不覆盖);
  4. done 终态 → skipped(exit 0,不执行);
  5. 留痕事件可被 state_reader 读回(可观测)。

全部用 fixture 工作区 + 注入桩 runner,不碰真实治理仓、不发真子进程。
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tools.turn_advancer.auto_executor import (
    REFUSE_EXIT_CODE,
    execute_auto,
    should_execute,
)
from tools.turn_advancer.state_reader import _normalize_event_type


def _runner(returncode=0, stdout="ok", stderr=""):
    """造一个桩 runner,记录被调命令,返回指定退出码/输出。"""
    calls: list[str] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout, stderr=stderr
        )

    return _run, calls


def _mcp_result(task_id="AIPOS-900", copyable="python3 _gate_call.py call lybra_x_dry_run --args '{}'"):
    return {
        "task_id": task_id,
        "current_status": "pending",
        "next_action": "claim_task",
        "rule": "queue_status=pending → claim",
        "requires_human_judgment": False,
        "command_type": "mcp_verb",
        "command": {"verb": "lybra_queue_claim_dry_run", "args": {"task_id": task_id}},
        "dispatch_mode": "auto",
        "copyable_line": copyable,
    }


def test_execute_success_exit_passthrough(tmp_path):
    """验收1:auto 真执行,成功 → executed / exit_code=0,前后各一条事件。"""
    runner, calls = _runner(returncode=0, stdout="done", stderr="")
    res = execute_auto(_mcp_result(), tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "executed"
    assert res["exit_code"] == 0
    assert calls == [_mcp_result()["copyable_line"]]  # 真把 copyable_line 喂给 runner
    before = Path(res["events"]["before"])
    after = Path(res["events"]["after"])
    assert before.is_file() and after.is_file()
    assert "auto_exec_start" in before.name
    assert "auto_exec_complete" in after.name


def test_execute_failure_exit_passthrough(tmp_path):
    """验收1:auto 真执行,失败 → 子进程退出码原样透传(非 0/1,用 3 防巧合)。"""
    runner, _ = _runner(returncode=3, stdout="", stderr="boom")
    res = execute_auto(_mcp_result(), tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "executed"
    assert res["exit_code"] == 3  # 透传,不是 0 也不是 1
    assert "failed" in res["reason"]


def test_refuse_requires_human_judgment(tmp_path):
    """验收2:requires_human_judgment=true → 拒绝执行,exit=REFUSE_EXIT_CODE,不调 runner。"""
    runner, calls = _runner(returncode=0)
    r = _mcp_result()
    r["requires_human_judgment"] = True
    r["human_judgment_reason"] = "审计卡内容由 executor 自产"
    res = execute_auto(r, tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "refused"
    assert res["exit_code"] == REFUSE_EXIT_CODE
    assert calls == []  # 未执行任何命令
    assert Path(res["events"]["after"]).is_file()  # 落了 refused 事件
    assert res["events"]["before"] is None


def test_refuse_human_placeholder(tmp_path):
    """验收2:copyable_line 含'等待人工'占位 → 拒绝执行。"""
    runner, calls = _runner(returncode=0)
    r = _mcp_result(copyable="lybra pump run --runtime-cmd <等待人工选择:无配置>")
    # 注意:resume 缺 runtime-cmd 时 command_builder 返回 command_type=wait_human;
    # 这里特意把 command_type 留成可执行类但命令含占位,验证占位判定独立生效。
    r["command_type"] = "cli"
    res = execute_auto(r, tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "refused"
    assert res["exit_code"] == REFUSE_EXIT_CODE
    assert calls == []


def test_refuse_wait_human_command_type(tmp_path):
    """验收2:command_type=wait_human → 拒绝执行。"""
    runner, calls = _runner(returncode=0)
    r = _mcp_result(copyable="等待人工判断（见 rule）")
    r["command_type"] = "wait_human"
    res = execute_auto(r, tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "refused"
    assert res["exit_code"] == REFUSE_EXIT_CODE
    assert calls == []


def test_skip_done(tmp_path):
    """done 终态 → skipped / exit 0,不执行,不落 refused 事件。"""
    runner, calls = _runner(returncode=0)
    r = _mcp_result(copyable="任务已完成，无下一步")
    r["command_type"] = "done"
    res = execute_auto(r, tmp_path, actor="tester", runner=runner)
    assert res["execution_status"] == "skipped"
    assert res["exit_code"] == 0
    assert calls == []
    assert res["events"]["after"] is None  # done 不留 refused 痕


def test_events_append_only(tmp_path):
    """验收3:同一秒内连续执行两次,before 事件不覆盖(append-only,撞名加序号)。"""
    fixed = [datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)]

    def clock():
        return fixed[0]

    runner, _ = _runner(returncode=0)
    res1 = execute_auto(_mcp_result(), tmp_path, actor="t", runner=runner, now=clock)
    res2 = execute_auto(_mcp_result(), tmp_path, actor="t", runner=runner, now=clock)
    b1 = Path(res1["events"]["before"])
    b2 = Path(res2["events"]["before"])
    assert b1.is_file() and b2.is_file()
    assert b1 != b2  # 不同文件,未覆盖
    # 目录里至少 4 个事件文件(两次 × before+after)
    ev_dir = tmp_path / "5_tasks" / "records" / "events" / "AIPOS-900"
    assert len(list(ev_dir.glob("*.md"))) >= 4


def test_event_readable_by_state_reader(tmp_path):
    """验收(可观测):留痕事件 frontmatter 能被 state_reader._normalize_event_type 读回。"""
    runner, _ = _runner(returncode=0)
    res = execute_auto(_mcp_result(), tmp_path, actor="t", runner=runner)
    after = Path(res["events"]["after"])
    text = after.read_text(encoding="utf-8")
    # 简易取 frontmatter(不引整个 frontmatter 解析器,保持测试轻)
    fm_block = text.split("---", 2)[1]
    fm = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'")
    assert _normalize_event_type(fm, after.name) == "auto_exec_complete"


def test_should_execute_table():
    """should_execute 决策表全扫(无副作用,不需 fixture)。"""
    cases = [
        ({"requires_human_judgment": True, "command_type": "mcp_verb",
          "copyable_line": "x"}, False),
        ({"requires_human_judgment": False, "command_type": "wait_human",
          "copyable_line": "x"}, False),
        ({"requires_human_judgment": False, "command_type": "done",
          "copyable_line": "x"}, False),
        ({"requires_human_judgment": False, "command_type": "unknown",
          "copyable_line": "x"}, False),
        ({"requires_human_judgment": False, "command_type": "cli",
          "copyable_line": "等待人工判断"}, False),
        ({"requires_human_judgment": False, "command_type": "mcp_verb",
          "copyable_line": ""}, False),
        ({"requires_human_judgment": False, "command_type": "mcp_verb",
          "copyable_line": "python3 gate.py call x"}, True),
        ({"requires_human_judgment": False, "command_type": "cli",
          "copyable_line": "lybra pump run --card X"}, True),
    ]
    for result, expected in cases:
        ok, _ = should_execute(result)
        assert ok is expected, f"{result} → expected {expected}, got {ok}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
