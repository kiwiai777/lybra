"""AIPOS-340 — Turn Advancer: 回合推进器（下一步命令解析器）。

给定任务状态，产生下一步完整可执行命令（auto 执行 / manual 打印），
覆盖完整回合链（执行/审计/fix/复审/finalize/部署/治理更新），
判断留人（卡内容/审计结论/owner_verify 不代产），有界与留痕。

与 330 gate_guidance 的区别:
- 330: agent 侧，"我该调什么动词"
- 340: dispatch 侧，"下一步完整命令（所有参数填好）"
"""

from .resolver import resolve_next_command

__all__ = ["resolve_next_command"]
