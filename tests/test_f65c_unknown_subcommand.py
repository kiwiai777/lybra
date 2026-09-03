#!/usr/bin/env python3
"""AIPOS-F65C 件③: 未知子命令必须出声的测试。

验收:未知子命令出声夹具。
"""
import subprocess
import sys


def test_unknown_top_level_command():
    """未知顶级命令:应输出错误"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "notexist"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    assert result.returncode != 0, "未知命令应返回非零退出码"
    assert "invalid choice" in result.stderr.lower() or "unknown command" in result.stderr.lower(), \
        f"应输出错误信息,实际 stderr: {result.stderr}"


def test_unknown_agent_subcommand():
    """agent 未知子命令:应输出错误"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "badsubcmd"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    assert result.returncode != 0, "未知 agent 子命令应返回非零退出码"
    assert "invalid choice" in result.stderr.lower() or "usage" in result.stderr.lower(), \
        f"应输出错误/用法信息,实际 stderr: {result.stderr}"


def test_unknown_board_subcommand():
    """board 未知子命令:应输出错误"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "board", "badsubcmd"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    assert result.returncode != 0, "未知 board 子命令应返回非零退出码"
    assert "invalid choice" in result.stderr.lower(), \
        f"应输出错误信息,实际 stderr: {result.stderr}"


def test_unknown_queue_subcommand():
    """queue 未知子命令:应输出错误"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "queue", "badsubcmd"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    assert result.returncode != 0, "未知 queue 子命令应返回非零退出码"
    assert "invalid choice" in result.stderr.lower(), \
        f"应输出错误信息,实际 stderr: {result.stderr}"


def test_unknown_draft_subcommand():
    """draft 未知子命令:应输出错误"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "draft", "badsubcmd"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    assert result.returncode != 0, "未知 draft 子命令应返回非零退出码"
    # draft 有显式的 "Unknown draft command" 处理
    assert "unknown" in result.stderr.lower() or "invalid choice" in result.stderr.lower(), \
        f"应输出错误信息,实际 stderr: {result.stderr}"


def test_no_silent_swallow():
    """确保未知命令不被静默吞掉(有输出)"""
    result = subprocess.run(
        [sys.executable, "-m", "tools.aipos_cli.aipos_cli", "silenttest"],
        capture_output=True,
        text=True,
        cwd="/home/kiwi/projects/lybra"
    )
    # 必须有输出(stderr 或 stdout)
    assert result.stderr or result.stdout, "未知命令不应静默(必须有输出)"
    # 必须是错误退出
    assert result.returncode != 0, "未知命令应返回非零退出码"


if __name__ == "__main__":
    print("Running AIPOS-F65C 件③ unknown subcommand tests...")
    
    test_unknown_top_level_command()
    print("✓ test_unknown_top_level_command")
    
    test_unknown_agent_subcommand()
    print("✓ test_unknown_agent_subcommand")
    
    test_unknown_board_subcommand()
    print("✓ test_unknown_board_subcommand")
    
    test_unknown_queue_subcommand()
    print("✓ test_unknown_queue_subcommand")
    
    test_unknown_draft_subcommand()
    print("✓ test_unknown_draft_subcommand")
    
    test_no_silent_swallow()
    print("✓ test_no_silent_swallow")
    
    print("\n✅ All unknown subcommand tests passed!")
