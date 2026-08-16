#!/usr/bin/env python3
"""
AIPOS-R6Q 靶④: 收账条目生成工具

用途: 为任务卡生成 FOUNDATION-BACKLOG.md 条目,按 schema 拼含完整 task_id 的条目并追加。
顾问不再手写台账条目(简称易被 R6M 校验拒绝),改用本工具生成完整 id。

用法:
  python3 tools/generate_backlog_entry.py <task_id> [--description "描述"] [--governance-root <path>]
  
示例:
  python3 tools/generate_backlog_entry.py AIPOS-R6Q --description "配置解析族收尾四靶"
"""

import argparse
import os
import sys
from datetime import datetime


def generate_backlog_entry(task_id: str, description: str = "") -> str:
    """生成符合格式的台账条目"""
    timestamp = datetime.now().strftime("%Y-%m-%d")
    
    # 基础条目格式(按现有 FOUNDATION-BACKLOG.md 格式)
    entry_lines = [
        f"- **{task_id}",
    ]
    
    if description:
        entry_lines[0] += f" {description}"
    
    entry_lines[0] += "**"
    
    # 可以根据需要扩展格式,例如添加状态、日期等
    # 当前保持简单,与现有格式对齐
    
    return "\n".join(entry_lines) + "\n"


def append_to_backlog(task_id: str, description: str, governance_root: str):
    """追加条目到 FOUNDATION-BACKLOG.md"""
    backlog_path = os.path.join(governance_root, "governance/FOUNDATION-BACKLOG.md")
    
    if not os.path.exists(backlog_path):
        print(f"错误: FOUNDATION-BACKLOG.md 不存在: {backlog_path}", file=sys.stderr)
        sys.exit(1)
    
    # 读取现有内容,检查是否已存在
    with open(backlog_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    if f"**{task_id}" in content:
        print(f"警告: 任务 {task_id} 已存在于 FOUNDATION-BACKLOG.md 中", file=sys.stderr)
        print(f"路径: {backlog_path}")
        return
    
    # 生成条目
    entry = generate_backlog_entry(task_id, description)
    
    # 追加到文件末尾
    with open(backlog_path, "a", encoding="utf-8") as f:
        # 确保文件末尾有换行
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(entry)
    
    print(f"✓ 已添加条目到 FOUNDATION-BACKLOG.md:")
    print(f"  任务ID: {task_id}")
    if description:
        print(f"  描述: {description}")
    print(f"  路径: {backlog_path}")


def main():
    parser = argparse.ArgumentParser(
        description="生成 FOUNDATION-BACKLOG.md 收账条目(按完整 task_id 格式)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s AIPOS-R6Q --description "配置解析族收尾四靶"
  %(prog)s AIPOS-R6S --governance-root ~/ai-project-os/2_projects/lybra
        """
    )
    
    parser.add_argument(
        "task_id",
        help="任务ID(完整格式,如 AIPOS-R6Q)",
    )
    
    parser.add_argument(
        "--description",
        "-d",
        default="",
        help="任务描述(可选)",
    )
    
    parser.add_argument(
        "--governance-root",
        "-g",
        default=None,
        help="治理工作区根目录(默认从环境变量 LYBRA_WORKSPACE_ROOT 读取)",
    )
    
    args = parser.parse_args()
    
    # 解析治理根目录
    governance_root = args.governance_root
    if not governance_root:
        governance_root = os.environ.get("LYBRA_WORKSPACE_ROOT")
    
    if not governance_root:
        print("错误: 无法确定治理工作区根目录", file=sys.stderr)
        print("请通过 --governance-root 参数或 LYBRA_WORKSPACE_ROOT 环境变量指定", file=sys.stderr)
        sys.exit(1)
    
    # 验证任务ID格式(基础检查)
    task_id = args.task_id.strip().upper()
    if not task_id:
        print("错误: 任务ID不能为空", file=sys.stderr)
        sys.exit(1)
    
    # 追加条目
    append_to_backlog(task_id, args.description, governance_root)


if __name__ == "__main__":
    main()
