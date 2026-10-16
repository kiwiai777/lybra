#!/usr/bin/env python3
"""AIPOS-F169 夹具: 假 `pi` 可执行(禁调真实 pi / 不发网络请求)。由 install_fake_pi 写成 PATH 里名为 pi 的薄壳调用本文件。

  pi --list-models [search]   按 FAKE_PI_MODELS(逗号分隔 <provider>/<model>)打印与真实 pi 同形的表(首行表头 provider model …);
                              FAKE_PI_LIST_EXIT 非 0 = 往 stderr 写一行后以该码退出; FAKE_PI_LIST_NOHEADER=1 = 不打表头。
  pi -p <kickoff> --mode json [--model <id>]
                              = loop 按 enums.schema harness pi launch 模板拉起: 收到的 argv(不含 kickoff 正文)落
                              FAKE_HARNESS_LOG/<卡号>.argv.json, 然后 exec tests/fake_harness.py <kickoff>(F95 假 harness 干活)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FAKE_HARNESS = Path(__file__).resolve().parent / "fake_harness.py"


def list_models(argv: list[str]) -> int:
    code = int(os.environ.get("FAKE_PI_LIST_EXIT") or 0)
    if code:
        sys.stderr.write("fake pi: list-models failed (fixture)\n")
        return code
    models = [m.strip() for m in (os.environ.get("FAKE_PI_MODELS") or "").split(",") if m.strip()]
    search = argv[argv.index("--list-models") + 1] if argv.index("--list-models") + 1 < len(argv) else ""
    if os.environ.get("FAKE_PI_LIST_NOHEADER") != "1":
        print(f"{'provider':<10}{'model':<24}{'context':<9}{'max-out':<9}{'thinking':<10}images")
    for ident in models:
        provider, _, model = ident.partition("/")
        if search and search.lower() not in ident.lower():
            continue
        print(f"{provider:<10}{model:<24}{'100K':<9}{'128K':<9}{'no':<10}yes")
    return 0


def launch(argv: list[str]) -> int:
    kickoff = argv[argv.index("-p") + 1]
    card = next((ln.split("已认领任务卡 ", 1)[1].split("。", 1)[0] for ln in kickoff.splitlines() if "已认领任务卡 " in ln), "unknown")
    log = os.environ["FAKE_HARNESS_LOG"]
    os.makedirs(log, exist_ok=True)
    shown = ["<kickoff>" if a == kickoff else a for a in argv]
    with open(os.path.join(log, f"{card}.argv.json"), "w", encoding="utf-8") as fh:
        json.dump(["pi", *shown], fh, ensure_ascii=False)
    os.execv(sys.executable, [sys.executable, str(FAKE_HARNESS), kickoff])
    return 0  # 不可达(execv 替换进程)


def main(argv: list[str]) -> int:
    if "--list-models" in argv:
        return list_models(argv)
    if "-p" in argv:
        return launch(argv)
    sys.stderr.write(f"fake pi: unsupported argv {argv!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
