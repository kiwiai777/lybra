#!/usr/bin/env python3
"""AIPOS-F110 夹具: ssh 执行器替身(声明式; 禁连任何真实主机)。

测试把本脚本以文件名 `ssh` 链到 PATH 首位: 产品按 verbs.schema lybra_loop.launch.remote.ssh_argv[0]="ssh" 经 PATH 解析,
拉起 / 探测 / 清理 / 反向检查全部走同一 transport 代码路径, 只是 ssh 本身换成本替身。
声明 = 环境变量 FAKE_SSH_DECL 指向的 JSON 文件:
  {"hosts": {"<ssh 目标>": {"reachable": true|false}}, "log": "<jsonl 日志路径>"}
行为 = 真 ssh 的远端语义:
  - 跳过选项(-T 等无参开关; -o/-p/-i/-l/-F/-J 等带参选项连同其值), 取 ssh 目标与远端命令串(其余参数以空格连接, 同 ssh);
  - 目标未声明或 reachable=false → stderr 仿 ssh 连接失败, exit 255(不执行任何命令);
  - 否则在本机以 `sh -c <远端命令串>` 执行(= 远端登录 shell 只做去引号与分词), stdin/stdout/stderr 继承(= ssh 会话管道,
    kickoff 经 stdin 原字节到达远端脚本)。
每次调用追加一行 JSON 日志 {host, command, reachable}(供夹具断言: kickoff 不进命令行、清理发了 TERM/KILL)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_OPTS_WITH_VALUE = set("BbcDEeFIiJLlmOoPpQRSWw")


def main() -> int:
    args = sys.argv[1:]
    i = 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        flag = args[i]
        if len(flag) == 2 and flag[1] in _OPTS_WITH_VALUE:
            i += 2
        else:
            i += 1
    if i < len(args) and args[i] == "--":
        i += 1
    if i >= len(args):
        sys.stderr.write("fake ssh: usage: ssh [options] destination command\n")
        return 255
    host, command = args[i], " ".join(args[i + 1:])
    decl = json.loads(Path(os.environ["FAKE_SSH_DECL"]).read_text(encoding="utf-8"))
    reachable = bool((decl.get("hosts") or {}).get(host, {}).get("reachable"))
    with open(decl["log"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"host": host, "command": command, "reachable": reachable}, ensure_ascii=False) + "\n")
    if not reachable:
        sys.stderr.write(f"ssh: connect to host {host} port 22: Connection refused\n")
        return 255
    if not command:
        sys.stderr.write("fake ssh: interactive session not supported\n")
        return 255
    os.execvp("sh", ["sh", "-c", command])
    return 255  # execvp 不返回


if __name__ == "__main__":
    sys.exit(main())
