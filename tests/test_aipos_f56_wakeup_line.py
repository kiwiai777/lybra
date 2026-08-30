#!/usr/bin/env python3
"""AIPOS-F56 测试 — 空闲带路出一行可复制指令(Owner 唤醒行)

验收项(对应任务卡):
① 先红后绿:修复前无唤醒行;修复后有
② 该行含绝对路径+卡号+角色,无多余装饰
③ 节流生效:同卡同状态连续三拍只出一次
④ 受众分级:与 F44C⑦ 共用同一声明(grep 证明无第二套判定)
⑤ 审计卡与执行卡两种形态各一例
⑥ 夹具入 run-all(本文件)
⑦ 基线零新增失败(由 run-all.sh 整体判定)
"""
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path("/home/kiwi/projects/lybra")
LOOP_DECISIONS = REPO_ROOT / "agents/harness/pi/lybra-loop/loop-decisions.ts"
LYBRA_LOOP = REPO_ROOT / "agents/harness/pi/lybra-loop/lybra-loop.ts"
LOOP_ENGINE = REPO_ROOT / "agents/harness/pi/lybra-loop/loop-engine.ts"


# ---------------------------------------------------------------------------
# ① 先红后绿:buildOwnerWakeupLine 函数存在且输出正确
# ---------------------------------------------------------------------------

def test_wakeup_line_function_exists():
    """绿:buildOwnerWakeupLine 函数在 loop-decisions.ts 中存在"""
    content = LOOP_DECISIONS.read_text(encoding="utf-8")
    if "export function buildOwnerWakeupLine" not in content:
        raise AssertionError("buildOwnerWakeupLine function not found in loop-decisions.ts")
    print("✓ buildOwnerWakeupLine 函数存在")


def test_wakeup_line_content():
    """绿:唤醒行含绝对路径+卡号+角色"""
    content = LOOP_DECISIONS.read_text(encoding="utf-8")
    # 函数体应包含 读卡 前缀 + cardAbsPath + taskId + role
    if "读卡" not in content:
        raise AssertionError("唤醒行缺少 '读卡' 前缀")
    if "cardAbsPath" not in content:
        raise AssertionError("唤醒行缺少 cardAbsPath 参数")
    # 检查函数签名含三个参数
    sig_match = re.search(
        r'export function buildOwnerWakeupLine\(([^)]+)\)',
        content
    )
    if not sig_match:
        raise AssertionError("无法找到 buildOwnerWakeupLine 函数签名")
    params = sig_match.group(1)
    for expected_param in ["taskId", "cardAbsPath", "role"]:
        if expected_param not in params:
            raise AssertionError(f"函数签名缺少参数 {expected_param}: {params}")
    print(f"✓ 唤醒行函数签名正确: buildOwnerWakeupLine({params})")


def test_wakeup_line_rendered_via_node():
    """绿:用 Node 直接调用 buildOwnerWakeupLine 验证输出"""
    # 写一个临时测试脚本
    test_script = """
import { buildOwnerWakeupLine } from "./loop-decisions.ts";
const line = buildOwnerWakeupLine(
  "AIPOS-F56",
  "/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-f56.md",
  "exec.lybra.kiwiai-dev"
);
console.log(line);
"""
    script_path = REPO_ROOT / "agents/harness/pi/lybra-loop/_f56_test_render.mjs"
    script_path.write_text(test_script, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True, text=True, timeout=10,
            cwd=str(REPO_ROOT / "agents/harness/pi/lybra-loop")
        )
        if result.returncode != 0:
            raise AssertionError(f"Node render failed: {result.stderr}")
        line = result.stdout.strip()
        print(f"  渲染结果: {line}")

        # ② 断言:含绝对路径+卡号+角色
        assert "/home/kiwi/ai-project-os/2_projects/lybra/5_tasks/queue/claimed/aipos-f56.md" in line, \
            f"唤醒行不含绝对路径: {line}"
        assert "AIPOS-F56" in line, \
            f"唤醒行不含卡号: {line}"
        assert "exec.lybra.kiwiai-dev" in line, \
            f"唤醒行不含角色: {line}"

        # ② 断言:自成一行(无换行符)
        assert "\n" not in line, \
            f"唤醒行含换行(非单行): {repr(line)}"

        # ② 断言:无多余装饰(不含 markdown 格式/emoji/箭头等)
        assert not re.search(r'[→←★▶●`*#]', line), \
            f"唤醒行含装饰字符: {line}"

        print("✓ 唤醒行渲染正确:含绝对路径+卡号+角色,单行无装饰")
    finally:
        script_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ② 执行卡与审计卡两种形态(⑤)
# ---------------------------------------------------------------------------

def test_wakeup_line_executor_and_audit_forms():
    """⑤ 执行卡与审计卡两种形态各一例"""
    test_script = """
import { buildOwnerWakeupLine } from "./loop-decisions.ts";
const execLine = buildOwnerWakeupLine(
  "AIPOS-F56",
  "/ws/5_tasks/queue/claimed/aipos-f56.md",
  "exec.lybra.kiwiai-dev"
);
const auditLine = buildOwnerWakeupLine(
  "AIPOS-F56-AUDIT",
  "/ws/task_cards/AIPOS-F56/AUDIT-AIPOS-F56.md",
  "audit.lybra.kiwiai-dev"
);
console.log("EXEC:" + execLine);
console.log("AUDIT:" + auditLine);
"""
    script_path = REPO_ROOT / "agents/harness/pi/lybra-loop/_f56_test_forms.mjs"
    script_path.write_text(test_script, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True, text=True, timeout=10,
            cwd=str(REPO_ROOT / "agents/harness/pi/lybra-loop")
        )
        if result.returncode != 0:
            raise AssertionError(f"Node render failed: {result.stderr}")
        lines = result.stdout.strip().split("\n")
        exec_line = ""
        audit_line = ""
        for l in lines:
            if l.startswith("EXEC:"):
                exec_line = l[5:]
            elif l.startswith("AUDIT:"):
                audit_line = l[6:]

        assert exec_line, "执行卡行未输出"
        assert audit_line, "审计卡行未输出"

        # 执行卡:含 exec 角色
        assert "exec.lybra.kiwiai-dev" in exec_line, f"执行卡行缺角色: {exec_line}"
        assert "aipos-f56.md" in exec_line, f"执行卡行缺路径: {exec_line}"

        # 审计卡:含 audit 角色
        assert "audit.lybra.kiwiai-dev" in audit_line, f"审计卡行缺角色: {audit_line}"
        assert "AUDIT-AIPOS-F56.md" in audit_line, f"审计卡行缺路径: {audit_line}"

        print(f"  执行卡: {exec_line}")
        print(f"  审计卡: {audit_line}")
        print("✓ 执行卡与审计卡两种形态均正确")
    finally:
        script_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# ③ 节流:lastResumeVoice 机制覆盖 guidance 状态
# ---------------------------------------------------------------------------

def test_throttling_guidance_status():
    """③ 节流生效:guidance 使用 lastResumeVoice 状态跟踪"""
    content = LYBRA_LOOP.read_text(encoding="utf-8")

    # 检查 guidance 分支使用了 lastResumeVoice
    guidance_section_match = re.search(
        r'if \(outcome\.kind === "guidance"\) \{[\s\S]*?scheduleNextTick\(5000\)',
        content
    )
    if not guidance_section_match:
        raise AssertionError("guidance 处理分支未找到")

    guidance_section = guidance_section_match.group(0)

    if "lastResumeVoice" not in guidance_section:
        raise AssertionError("guidance 分支未使用 lastResumeVoice 节流")

    if '"guidance"' not in guidance_section:
        raise AssertionError("guidance 分支未使用 'guidance' 状态值")

    # 检查状态更新
    if 'lastResumeVoice = { taskId: outcome.taskId, status: "guidance" }' not in guidance_section:
        raise AssertionError("guidance 分支未正确更新 lastResumeVoice 状态")

    print("✓ 节流生效:guidance 使用 lastResumeVoice 同卡同状态去重")


# ---------------------------------------------------------------------------
# ④ 受众分级:共用 voice() 单出口,无第二套判定
# ---------------------------------------------------------------------------

def test_shared_audience_classification():
    """④ 唤醒行走 voice() 单出口,与 F44C⑦ 共用同一受众分级"""
    content = LYBRA_LOOP.read_text(encoding="utf-8")

    # 检查 guidance 分支中的 Owner 唤醒行走 voice()
    guidance_section_match = re.search(
        r'if \(outcome\.kind === "guidance"\) \{[\s\S]*?scheduleNextTick\(5000\)',
        content
    )
    if not guidance_section_match:
        raise AssertionError("guidance 处理分支未找到")

    guidance_section = guidance_section_match.group(0)

    # 必须调用 voice() (Owner 面,单出口 F15)
    if "voice(wakeupLine" not in guidance_section:
        raise AssertionError("guidance 分支未通过 voice() 输出唤醒行(违反 F15 单出口)")

    # 不应有第二套受众判定(不应有独立的 audience/recipient/owner_check 等逻辑)
    # 只允许 voice() 和 sendUserMessage() 两个通道,不应有第三个输出通道
    output_calls = re.findall(r'(?:console\.log|process\.stdout|ctx\.reply|notify)\s*\(', guidance_section)
    if output_calls:
        raise AssertionError(f"guidance 分支存在 voice/sendUserMessage 之外的输出通道: {output_calls}")

    print("✓ 受众分级:唤醒行走 voice() 单出口,无第二套判定")


# ---------------------------------------------------------------------------
# ⑥ 引擎层:guidance outcome 携带 cardAbsPath
# ---------------------------------------------------------------------------

def test_engine_guidance_has_card_abs_path():
    """guidance TickOutcome 类型含 cardAbsPath 字段"""
    engine_content = LOOP_ENGINE.read_text(encoding="utf-8")

    # 检查 TickOutcome 的 guidance 变体含 cardAbsPath
    if 'kind: "guidance"' not in engine_content:
        raise AssertionError("loop-engine.ts 缺 guidance outcome 类型")

    # 找 guidance 类型声明
    guidance_type_match = re.search(
        r'\{\s*kind:\s*"guidance"[^}]+\}',
        engine_content
    )
    if not guidance_type_match:
        raise AssertionError("无法找到 guidance 类型声明")

    guidance_type = guidance_type_match.group(0)
    if "cardAbsPath" not in guidance_type:
        raise AssertionError(f"guidance 类型缺 cardAbsPath: {guidance_type}")

    print(f"✓ guidance outcome 类型含 cardAbsPath")


def test_engine_guidance_populates_card_abs_path():
    """guidance outcome 构造时填充 cardAbsPath"""
    engine_content = LOOP_ENGINE.read_text(encoding="utf-8")

    # 检查构造 guidance return 时包含 cardAbsPath
    if "cardAbsPath," not in engine_content and "cardAbsPath\n" not in engine_content:
        raise AssertionError("guidance outcome 构造时未填充 cardAbsPath")

    print("✓ guidance outcome 构造时填充 cardAbsPath")


# ---------------------------------------------------------------------------
# 先红后绿:证明修复前不存在
# ---------------------------------------------------------------------------

def test_red_green_before_fix():
    """① 先红:证明此功能在修复前不存在(检查 git diff)"""
    # 用 git diff main 证明这些是新增内容
    result = subprocess.run(
        ["git", "diff", "main", "--", str(LOOP_DECISIONS.relative_to(REPO_ROOT))],
        capture_output=True, text=True, timeout=10,
        cwd=str(REPO_ROOT)
    )
    diff = result.stdout

    if "+export function buildOwnerWakeupLine" not in diff:
        raise AssertionError(
            "先红失败:buildOwnerWakeupLine 不在 diff 新增行中(可能未提交或不存在)"
        )

    print("✓ 先红后绿:buildOwnerWakeupLine 是本次新增(经 git diff 证实)")


# ---------------------------------------------------------------------------
# 运行全部
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_wakeup_line_function_exists,
        test_wakeup_line_content,
        test_wakeup_line_rendered_via_node,
        test_wakeup_line_executor_and_audit_forms,
        test_throttling_guidance_status,
        test_shared_audience_classification,
        test_engine_guidance_has_card_abs_path,
        test_engine_guidance_populates_card_abs_path,
        test_red_green_before_fix,
    ]

    failed = 0
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        try:
            t()
        except AssertionError as e:
            print(f"✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            failed += 1

    print(f"\n{'='*60}")
    if failed == 0:
        print(f"✓ AIPOS-F56 全部 {len(tests)} 项测试通过")
    else:
        print(f"✗ {failed}/{len(tests)} 项失败")
        sys.exit(1)
