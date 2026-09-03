#!/usr/bin/env bash
# AIPOS-F65C 测试套件 - 运行所有三件的测试
set -e

cd "$(dirname "$0")/.."

echo "========================================================"
echo " AIPOS-F65C 测试套件"
echo "========================================================"
echo

echo "件① - 坏 frontmatter 卡修复通路"
echo "────────────────────────────────────────────────────────"
python3 tests/test_f65c_frontmatter_repair.py
echo

echo "件③ - 未知子命令必须出声"
echo "────────────────────────────────────────────────────────"
python3 tests/test_f65c_unknown_subcommand.py
echo

echo "件④ - 占位符检测区分引用与实际空白"
echo "────────────────────────────────────────────────────────"
python3 tests/test_f65c_placeholder_detection.py
echo

echo "========================================================"
echo " ✅ AIPOS-F65C 全部测试通过"
echo "========================================================"
