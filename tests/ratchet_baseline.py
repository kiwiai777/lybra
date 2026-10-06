"""AIPOS-F109 件⑤: 棘轮基线文件(只存条目、一行一条、稳定排序)的唯一读写口——f87 碎片化棘轮与 f96 文档棘轮共用。

原状(10-04 并行合并实证): 基线 JSON 带派生字段(total、invariants.*.count)与叙述(baseline_commit_base), 两张卡各删不同条目
也会在这些字段上「同键异值」冲突 → finalize 合并失败、裁决作废重审。
现: 基线文件 = JSON Lines, 每行一个条目对象(键排序的规范序列化), 全文件按行文本排序; 不存任何派生量与叙述(计数由夹具运行时
计算, 删条历史看 git log)。两分支各删不同(不相邻)条目 = 改不同行 → git 三方合并无冲突。相邻两行同时被删仍会冲突(git 行级
合并对相邻改动的固有行为), 解法 = 两行都删。
"""
from __future__ import annotations

import json
from pathlib import Path


def canonical_line(entry: dict[str, object]) -> str:
    """条目的规范一行: 键排序、非 ASCII 原样、固定分隔符。"""
    return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def render_entries(entries: list[dict[str, object]]) -> str:
    """条目集 → 文件全文(按规范行排序, 每行一条, 末尾换行; 空集 = 空文件)。"""
    lines = sorted(canonical_line(entry) for entry in entries)
    return "".join(line + "\n" for line in lines)


def read_entries(path: Path) -> list[dict[str, object]]:
    """读基线条目。形坏 fail-closed(ValueError): 非对象行 / 空行 / 非规范序列化 / 未排序——保证文件始终一行一条、稳定排序。"""
    text = Path(path).read_text(encoding="utf-8")
    entries: list[dict[str, object]] = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} 不是 JSON 行: {exc}") from exc
        if not isinstance(entry, dict) or not entry:
            raise ValueError(f"{path}:{lineno} 须为非空 JSON 对象(一行一个条目)")
        if line != canonical_line(entry):
            raise ValueError(f"{path}:{lineno} 非规范序列化(键排序、分隔符 ', ' ': '); 应为: {canonical_line(entry)}")
        entries.append(entry)
    if lines != sorted(lines):
        raise ValueError(f"{path} 行未按文本排序(稳定排序是行级合并的前提)")
    if text and not text.endswith("\n"):
        raise ValueError(f"{path} 末行缺换行")
    return entries
