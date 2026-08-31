"""AIPOS-F57 — 从 0 接新项目全流程固化(onboarding guide generator)。

把"从项目注册到首卡开跑"的六步做成产品命令, 全程零手工编辑:
  ① 项目注册(治理根+project.json)
  ② 信封铸造(执行/审计各一, 按项目域)
  ③ 三角色发码(executor/auditor/advisor)
  ④ 一条 enroll 配齐(F54/F54-fix1 已覆盖)
  ⑤ 起 pi 三步
  ⑥ 首卡开跑自检

每步失败都报错带路且给出可执行出口。
禁任何项目名/路径硬编码(项目无关性)。

单源纪律:
  - 命令模板从本模块生成(禁 skill 硬编码)
  - 可启动最小集清单 = distribution.schema#minimum_bootable_set(单源)
  - 接线规格 = workstation_wiring.py(单源)
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 六步 onboarding guide 生成
# ---------------------------------------------------------------------------

def _shell_quote(s: str) -> str:
    """Shell-quote a string for copy-paste commands."""
    return shlex.quote(s)


def generate_onboarding_guide(
    project_name: str,
    *,
    home_root: str | None = None,
    gate_url: str | None = None,
    code_repo: str | None = None,
    actor: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """生成从 0 接新项目全流程的分步指南。

    所有值参数化, 禁硬编码。返回结构化 guide(steps[] + metadata)。
    每步包含: step_number, title, command, purpose, check, on_fail。
    """
    # 默认值推导(禁硬编码)
    _home_root = home_root or os.environ.get("LYBRA_HOME_ROOT") or str(Path("~/.lybra/projects").expanduser())
    _gate_url = gate_url or os.environ.get("LYBRA_GATE_URL") or "http://127.0.0.1:7118"
    _actor = actor or os.environ.get("USER") or "owner"
    _now = datetime.now(timezone.utc)
    _expires = (_now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _policy_id = f"pol_{project_name}_1"

    steps: list[dict[str, Any]] = []

    # ── Step 1: 项目注册 ──────────────────────────────────────────
    step1_cmd_parts = [
        "lybra", "project", "new",
        _shell_quote(project_name),
        "--home-root", _shell_quote(_home_root),
        "--actor", _shell_quote(_actor),
    ]
    if code_repo:
        step1_cmd_parts.extend(["--code-repo", _shell_quote(code_repo)])

    steps.append({
        "step_number": 1,
        "title": "项目注册(治理根 + project.json)",
        "command": " ".join(step1_cmd_parts),
        "purpose": f"在治理根 {_home_root} 下创建 {project_name} 的完整项目树(队列/记录/治理)和 project.json",
        "check": "命令输出 'Created project root:' 即成功; 验证: lybra project list 应出现项目名",
        "on_fail": {
            "PROJECT_NAME_EMPTY": "项目名不能为空; 给一个非空名字",
            "PROJECT_EXISTS": "项目已存在; 用 lybra project list 确认后跳到 Step 2",
            "gate 连接失败": "确认 lybra serve 已启动: lybra serve --help; 启动后再跑此命令",
        },
        "creates": f"{_home_root}/{project_name}/",
    })

    # ── Step 2: 信封铸造(executor + auditor) ─────────────────────
    project_root = f"{_home_root}/{project_name}"

    step2_exec_cmd = " ".join([
        "lybra", "envelope", "mint",
        "--policy-id", _shell_quote(_policy_id),
        "--agent-or-role", "executor",
        "--max-tasks", "50",
        "--task-mode", "code",
        "--expires-at", _shell_quote(_expires),
        "--decision-summary", _shell_quote(f"New project {project_name}: executor autonomy envelope"),
        "--actor", _shell_quote(_actor),
        "--json",
    ])

    step2_audit_cmd = " ".join([
        "lybra", "envelope", "mint",
        "--policy-id", _shell_quote(f"pol_{project_name}_audit_1"),
        "--agent-or-role", "auditor",
        "--max-tasks", "50",
        "--task-mode", "code",
        "--expires-at", _shell_quote(_expires),
        "--decision-summary", _shell_quote(f"New project {project_name}: auditor autonomy envelope"),
        "--actor", _shell_quote(_actor),
        "--json",
    ])

    steps.append({
        "step_number": 2,
        "title": "信封铸造(执行 + 审计各一)",
        "commands": [step2_exec_cmd, step2_audit_cmd],
        "command": step2_exec_cmd,  # primary
        "purpose": f"为 {project_name} 铸造 executor 和 auditor 的 PreAuthorized 自治信封, 允许三角色在信封内自动认领任务",
        "check": "每条命令输出 JSON 含 ok=true; 验证: lybra envelope list(如有)应显示新信封",
        "on_fail": {
            "policy_id 冲突": "信封 ID 已存在; 换一个 policy_id(如加后缀 _v2)",
            "missing --owner-authorization-ref": "需要 owner 授权; 加 --actor owner 或提供 owner 授权引用",
            "gate 未运行": "先启动门: lybra serve",
        },
        "creates": f"{project_root}/5_tasks/policies/{_policy_id}.md",
    })

    # ── Step 3: 三角色发码 ────────────────────────────────────────
    step3_commands = []
    for role in ("executor", "auditor", "advisor"):
        cmd = " ".join([
            "lybra", "roles", "enroll-code",
            "--role", role,
            "--ttl", "86400",
            "--gate-url", _shell_quote(_gate_url),
            "--governance-root", _shell_quote(project_root),
            "--reason", _shell_quote(f"Onboarding {project_name} {role}"),
            "--json",
        ])
        step3_commands.append(cmd)

    steps.append({
        "step_number": 3,
        "title": "三角色发码(executor / auditor / advisor)",
        "commands": step3_commands,
        "command": step3_commands[0],  # primary
        "purpose": f"为 {project_name} 的三角色各生成一个一次性注册码(enrollment code), 用于 Step 4 兑换凭据",
        "check": "每条命令输出 JSON 含 enrollment_code 字段; 保存这些码供 Step 4 使用",
        "on_fail": {
            "no advisor token": "治理仓 connection.json 缺 advisor token; 先确认 lybra serve 已初始化该项目的角色",
            "gate 拒绝": "检查当前角色是否有 enroll-code 权限(advisor/owner 才可)",
            "governance-root 不存在": "Step 1 可能未成功; 回退执行 Step 1",
        },
        "creates": "三个 enrollment code(一次性, 24h 有效)",
    })

    # ── Step 4: 一条 enroll 配齐 ──────────────────────────────────
    _workspace = workspace_dir or f"~/{project_name}-workstation"

    step4_template = " ".join([
        "cd", _shell_quote(_workspace), "&&",
        "lybra", "roles", "enroll",
        "--code", "<ENROLLMENT_CODE>",
        "--workspace", _shell_quote(_workspace),
        "--verify",
    ])

    steps.append({
        "step_number": 4,
        "title": "一条 enroll 配齐(三角色各跑一次)",
        "command": step4_template,
        "purpose": f"在工位目录 {_workspace} 用 Step 3 的注册码兑换凭据, 落齐 .lybra/ 配置 + .pi/ 接线 + 可启动最小集(F54 覆盖)",
        "check": "命令输出 enroll 成功 + verify 通过; 验证: .lybra/connection.json 存在且含 lybra_bin 和正确的 workspace_root",
        "on_fail": {
            "code 过期/已用": "重新跑 Step 3 生成新码",
            "401 Unauthorized": "注册码无效或过期; 用 lybra roles enroll-list 检查状态, 必要时重新生成",
            ".pi/ 接线缺失": "F54 应自动落; 如缺失报 bug(lybra-onboarding skill 可辅助诊断)",
            "connection.json 缺 lybra_bin": "F54-fix1 应自动补; 如缺失报 bug",
            "workspace_root 写成 harness root": "F54-fix1 应校正; 如仍有问题报 bug",
            "role 缺 owner_policy_ref": "Step 2 信封可能未生效; 检查信封 status=active",
        },
        "creates": f"{_workspace}/.lybra/connection.json, {_workspace}/.lybra/role, {_workspace}/.pi/",
        "note": "三角色各跑一次(换不同 --code 和不同工位目录, 或同一目录切换角色)",
    })

    # ── Step 5: 起 pi 三步 ────────────────────────────────────────
    step5_commands = [
        f"cd {_shell_quote(_workspace)}",
        "# 确认 .pi/ 接线完整(应看到 settings.json, extensions/, skills/)",
        "ls -la .pi/",
        "# 起 pi(Pi 编码代理)",
        "pi",
        "# 在 pi 内运行 sync 拉取最新分发",
        "/lybra sync",
        "# 进入接活模式",
        "lybra on",
    ]

    steps.append({
        "step_number": 5,
        "title": "起 pi 三步(进工位 → sync → lybra on)",
        "command": "\n".join(step5_commands),
        "purpose": f"在工位 {_workspace} 启动 pi 编码代理, 同步分发, 进入接活模式",
        "check": "pi 成功启动 + /lybra sync 无报错 + lybra on 显示可认领任务或'暂无可认领'",
        "on_fail": {
            "pi 找不到": "确认 pi 已安装(npm i -g @earendil-works/pi-coding-agent)",
            ".pi/ 为空": "Step 4 enroll 未落 .pi/ 接线; 重跑 Step 4 或检查 F54 接线逻辑",
            "/lybra sync 失败": "检查 .lybra/connection.json 中 lybra_bin 指向的文件是否存在",
            "lybra on 报 token 错": "确认 .lybra/connection.json 中 token 有效; 必要时重跑 Step 4",
        },
        "creates": "运行中的 pi 会话 + lybra 接活循环",
    })

    # ── Step 6: 首卡开跑自检 ──────────────────────────────────────
    step6_cmd = " ".join([
        "lybra", "agent", "launch-check",
        "--gate-url", _shell_quote(_gate_url),
        "--workspace-root", _shell_quote(_workspace),
    ])

    steps.append({
        "step_number": 6,
        "title": "首卡开跑自检",
        "command": step6_cmd,
        "purpose": "验证工位可启动最小集完整(缺项逐项点名), 确认首卡可认领可执行",
        "check": "自检全绿(ok=true); 如有缺项会逐项点名",
        "on_fail": {
            "缺项报错": "按输出的缺项名逐项修复; 常见: lybra_bin 悬空→重新 enroll; owner_policy_ref 缺失→检查信封",
            "token 无效": "重跑 Step 4 的 enroll --verify",
            "gate 不通": "确认 lybra serve 运行中且 gate_url 正确",
        },
        "creates": "自检报告(全绿 = 从 0 到首卡开跑完成)",
    })

    return {
        "project_name": project_name,
        "home_root": _home_root,
        "gate_url": _gate_url,
        "generated_at": _now.isoformat(),
        "total_steps": len(steps),
        "steps": steps,
        "summary": (
            f"从 0 接新项目 {project_name} 全流程({len(steps)} 步, 零手工编辑):\n"
            + "\n".join(f"  Step {s['step_number']}: {s['title']}" for s in steps)
        ),
    }


def format_guide_text(guide: dict[str, Any]) -> str:
    """将结构化 guide 格式化为可读文本(终端输出用)。"""
    lines: list[str] = []
    lines.append(f"═══ 从 0 接新项目: {guide['project_name']} ═══")
    lines.append(f"治理根: {guide['home_root']}")
    lines.append(f"门地址: {guide['gate_url']}")
    lines.append(f"生成时间: {guide['generated_at']}")
    lines.append(f"共 {guide['total_steps']} 步, 全程零手工编辑")
    lines.append("")

    for step in guide["steps"]:
        lines.append(f"── Step {step['step_number']}: {step['title']} ──")
        lines.append(f"目的: {step['purpose']}")
        lines.append("")

        # 命令
        if "commands" in step and len(step["commands"]) > 1:
            lines.append("命令(逐条执行):")
            for i, cmd in enumerate(step["commands"], 1):
                lines.append(f"  {i}. {cmd}")
        else:
            lines.append("命令:")
            for cmd_line in step["command"].split("\n"):
                lines.append(f"  {cmd_line}")

        lines.append("")
        lines.append(f"验证: {step['check']}")

        if step.get("on_fail"):
            lines.append("失败出口:")
            for err, fix in step["on_fail"].items():
                lines.append(f"  [{err}] → {fix}")

        if step.get("note"):
            lines.append(f"注: {step['note']}")

        lines.append(f"产物: {step['creates']}")
        lines.append("")

    lines.append("═══ 完成: 从项目注册到首卡开跑, 零手工编辑 ═══")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 负夹具: 六步中任一步的错误路径验证
# ---------------------------------------------------------------------------

def validate_step_prerequisites(
    step_number: int,
    *,
    project_name: str,
    home_root: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """验证某一步的前置条件是否满足(用于负夹具测试和运行时前置检查)。

    返回 {ok: bool, missing: [str], guidance: str}。
    """
    _home_root = home_root or os.environ.get("LYBRA_HOME_ROOT") or str(Path("~/.lybra/projects").expanduser())
    project_root = Path(_home_root).expanduser() / project_name
    _workspace = workspace_dir or f"~/{project_name}-workstation"
    ws = Path(_workspace).expanduser()

    missing: list[str] = []
    guidance: list[str] = []

    if step_number >= 1:
        if not project_root.is_dir():
            missing.append("project_root")
            guidance.append(f"Step 1 未完成: 项目目录不存在 {project_root}; 先跑 lybra project new")
        elif not (project_root / "project.json").is_file():
            missing.append("project.json")
            guidance.append("project.json 缺失; 重跑 Step 1")

    if step_number >= 2:
        policies_dir = project_root / "5_tasks" / "policies"
        if policies_dir.is_dir():
            pol_files = list(policies_dir.glob("pol_*.md"))
            if not pol_files:
                missing.append("envelopes")
                guidance.append("Step 2 未完成: 无信封文件; 跑 lybra envelope mint")
        elif project_root.is_dir():
            missing.append("policies_dir")
            guidance.append(f"5_tasks/policies/ 目录不存在; 跑 Step 2")

    if step_number >= 4:
        conn_json = ws / ".lybra" / "connection.json"
        if not conn_json.is_file():
            missing.append("connection.json")
            guidance.append(f"Step 4 未完成: {conn_json} 不存在; 跑 lybra roles enroll")
        else:
            try:
                data = json.loads(conn_json.read_text(encoding="utf-8"))
                if not data.get("lybra_bin"):
                    missing.append("lybra_bin")
                    guidance.append("connection.json 缺 lybra_bin; F54-fix1 应自动补, 重跑 enroll")
                ws_root = data.get("workspace_root", "")
                gov_root = data.get("governance_root", "")
                if ws_root and gov_root:
                    if Path(ws_root).resolve() != Path(gov_root).resolve():
                        missing.append("workspace_root_mismatch")
                        guidance.append("workspace_root != governance_root; F54-fix1 应校正, 重跑 enroll")
            except (OSError, json.JSONDecodeError):
                missing.append("connection.json_invalid")
                guidance.append("connection.json 格式错误; 重跑 Step 4")

    if step_number >= 5:
        pi_settings = ws / ".pi" / "settings.json"
        if not pi_settings.is_file():
            missing.append(".pi/settings.json")
            guidance.append(".pi 接线缺失; Step 4 enroll 应自动落, 重跑 Step 4")

    if step_number >= 6:
        role_file = ws / ".lybra" / "role"
        if role_file.is_file():
            try:
                role_data = json.loads(role_file.read_text(encoding="utf-8"))
                if not role_data.get("owner_policy_ref"):
                    missing.append("owner_policy_ref")
                    guidance.append("role 文件缺 owner_policy_ref; 检查 Step 2 信封是否 active")
            except (OSError, json.JSONDecodeError):
                missing.append("role_invalid")
                guidance.append("role 文件格式错误; 重跑 Step 4")

    return {
        "ok": not missing,
        "missing": missing,
        "guidance": guidance,
    }
