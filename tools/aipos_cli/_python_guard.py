# -*- coding: utf-8 -*-
"""AIPOS-F263 件①: 入口解释器版本守卫(包 tools.aipos_cli 导入即判, 早于任何用到新语法的模块被编译)。

`lybra` 入口(bin/lybra)按 python3 / python 找解释器; 找到的解释器过旧时, 原先在导入 CLI 模块处抛语法错误栈。
本模块只用老解释器也能解析的语法(无 f-string / 类型注解), 版本要求读 config.schema setup_check.requirements.python.min
(与 `lybra setup check` 同一声明), 过旧 = 一段可读提示 + 退出码 1。声明读不出 = 不在此拦(交给 setup check 报)。
"""
import json
import os
import sys


def _declared_min():
    schema = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "schema",
                          "config.schema.json")
    try:
        with open(schema, "rb") as fh:
            raw = json.loads(fh.read().decode("utf-8"))["setup_check"]["requirements"]["python"]["min"]
        return tuple(int(part) for part in str(raw).split("."))
    except (IOError, OSError, ValueError, KeyError, TypeError):
        return None  # 声明读不出: 不在入口拦(setup check 会报声明错误)


def require_python(version_info=None, stream=None):
    """解释器版本低于声明 = 写可读提示并 SystemExit(1); 满足 / 声明读不出 = 返回 None。"""
    want = _declared_min()
    have = tuple(version_info if version_info is not None else sys.version_info)[:3]
    if want is None or tuple(have[:len(want)]) >= want:
        return None
    shown_have = ".".join(str(part) for part in have)
    shown_want = ".".join(str(part) for part in want)
    (stream or sys.stderr).write(
        "lybra: Python >= %s is required, but this interpreter is %s (%s).\n"
        "lybra: 需要 Python >= %s, 当前解释器 %s(%s)。出口: 安装 Python %s 或更高版本, 或设环境变量 LYBRA_PYTHON 指向合格解释器;"
        " 装好后跑 lybra setup check 自检。\n" % (shown_want, shown_have, sys.executable, shown_want, shown_have, sys.executable,
                                                  shown_want))
    raise SystemExit(1)


if __name__ == "__main__":  # 内部模块, 非命令行入口(同 _cli_entry_guard 语义; 本模块须老解释器可解析, 不导入它)
    sys.stderr.write("Error: tools.aipos_cli._python_guard is not a command-line entry point. Use the 'lybra' command.\n")
    raise SystemExit(1)
