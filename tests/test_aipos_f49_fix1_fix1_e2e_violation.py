#!/usr/bin/env python3
"""
AIPOS-F49-fix1-fix1 端到端测试夹具（违规，不入 run-all）

这个夹具故意不入 run-all.sh，用于测试自检门是否能检测到。
"""

def test_violation_fixture():
    """违规夹具：不入 run-all"""
    print("✓ 违规夹具通过（但不在 run-all 中）")

if __name__ == "__main__":
    test_violation_fixture()
    print("✓ AIPOS-F49-fix1-fix1 违规夹具通过")
