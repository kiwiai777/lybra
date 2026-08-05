"""AIPOS-332 S6②/S8/S12 — 运行体档案与产出位置档案(纯数据 + 纯函数).

把"差异"做成**数据**,不做成代码分支。两条维度:

  1. 运行体类型(runtime profile):该运行体的输出是否缓冲 → 决定能否用 run-log 判停滞。
  2. 产出位置(output-location profile):产物落在哪 → 决定 worktree 判据是否适用、
     该监听哪些观测面、expect 该从哪派生(产品仓 / 工作区 / 远端)。

**新增运行体或产出位置 = 加一条档案,零代码改动**(S6②/S8 验收)。
档案缺失时走**安全默认**(宁可多看几个观测面)并**明确提示**,不静默猜(S6②/S8 硬约束2)。

红线:
  - 纯 stdlib,无 gate / 无外部依赖(可被 stdlib-only 模块安全引用)。
  - 只做"选择与描述",不执行任何 IO / 不调用任何零件(零件独立可用,S6①)。
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 运行体档案:描述运行体的【输出行为】(是否缓冲)
# buffer_output=True 的运行体,其 stdout 被缓冲,run-log 在进程结束前恒不变 →
# 不得用 run-log mtime 判停滞(S2 病根)。
# ---------------------------------------------------------------------------

RUNTIME_PROFILES: dict[str, dict[str, Any]] = {
    "pi": {
        "buffer_output": True,
        "description": "pi coding agent (stdout buffered until flush/exit).",
        "stall_surfaces": ["session_dirs", "cpu", "worktree"],
        # run-log 仅可用于"结束检测"(进程退出会 flush),不可判停滞。
        "run_log_role": "end_only",
        # AIPOS-332F3 修一:会话根来自运行体档案,不硬编码 product_repo/.pi。
        # pi 的会话目录 = session_root + encode(cwd);cwd 即运行目录(产品仓)。
        "session_root": "~/.pi/agent/sessions",
        "session_dir_encoding": "pi_cwd_dash",  # / → -,前后加 --
        # AIPOS-332F4 修一:拉起时间参数(启动窗口、检查间隔、冷启动宽限)。
        # 慢端点无头冷启动实测 ~60s(网络等待期 CPU≈0),留裕量 ≥180s。
        # 代码不写死任何秒数——step_launch 从本字段取值。
        "launch_window_secs": 180,
        "check_interval_secs": 5,
        "cold_start_grace_secs": 120,
    },
    "cc": {
        "buffer_output": True,
        "description": "Claude Code harness (buffered output).",
        "stall_surfaces": ["session_dirs", "cpu", "worktree"],
        "run_log_role": "end_only",
        "session_root": None,  # cc 无已知会话目录机制
        "session_dir_encoding": None,
        "launch_window_secs": 180,
        "check_interval_secs": 5,
        "cold_start_grace_secs": 120,
    },
    "claude_code": {
        "buffer_output": True,
        "description": "Claude Code direct (buffered output).",
        "stall_surfaces": ["session_dirs", "cpu", "worktree"],
        "run_log_role": "end_only",
        "session_root": None,
        "session_dir_encoding": None,
        "launch_window_secs": 180,
        "check_interval_secs": 5,
        "cold_start_grace_secs": 120,
    },
    "generic_bash": {
        "buffer_output": False,
        "description": "Generic bash script (line-buffered stdout).",
        "stall_surfaces": ["run_log", "session_dirs", "worktree"],
        "run_log_role": "stall",
        "session_root": None,
        "session_dir_encoding": None,
        # bash 秒活,无冷启动延迟;窗口短。
        "launch_window_secs": 30,
        "check_interval_secs": 3,
        "cold_start_grace_secs": 10,
    },
}

# 安全默认(未知运行体):假设缓冲输出(不得用 run-log 判停滞)+ 多看几个面。
_DEFAULT_RUNTIME_PROFILE: dict[str, Any] = {
    "buffer_output": True,
    "description": "UNKNOWN runtime — safe default assumes buffered output.",
    "stall_surfaces": ["session_dirs", "cpu", "worktree"],
    "run_log_role": "end_only",
    "session_root": None,
    "session_dir_encoding": None,
    # AIPOS-332F4: 安全默认用大窗口(宁可多等,不误杀)。
    "launch_window_secs": 180,
    "check_interval_secs": 5,
    "cold_start_grace_secs": 120,
}


def get_runtime_profile(runtime_type: str | None) -> tuple[dict[str, Any], list[str]]:
    """返回 (profile, warnings)。未知运行体 → 安全默认 + 明确提示。"""
    warns: list[str] = []
    if not runtime_type:
        warns.append(
            "运行体类型未声明,已退到安全默认(假设输出缓冲,不用 run-log 判停滞)。"
        )
        return dict(_DEFAULT_RUNTIME_PROFILE), warns
    profile = RUNTIME_PROFILES.get(runtime_type)
    if profile is None:
        warns.append(
            f"未知运行体类型 {runtime_type!r},已退到安全默认(假设输出缓冲,不用 run-log 判停滞)。"
            " 新增运行体请在 RUNTIME_PROFILES 增加一条档案(零代码改动)。"
        )
        return dict(_DEFAULT_RUNTIME_PROFILE), warns
    return dict(profile), warns


# ---------------------------------------------------------------------------
# 产出位置档案:描述产物落在哪 → worktree 判据是否适用、监听哪些面、expect 从哪派生。
# 取值域对齐 AIPOS-304 D1 枚举(S12):新增位置 = 加一条档案。
# ---------------------------------------------------------------------------

OUTPUT_LOCATION_PROFILES: dict[str, dict[str, Any]] = {
    # 产物落在产品仓工作树 → worktree 判据适用。
    "product_repo_worktree": {
        "worktree_criterion": True,
        "monitors_product_repo": True,
        "monitors_workspace": True,  # 回路信号无条件落工作区(S10)
        "expect_source": "both",     # 产品仓产物 + 工作区事件
        "description": "产出在产品仓工作树(代码任务)。",
    },
    "workspace_records": {
        "worktree_criterion": False,
        "monitors_product_repo": False,
        "monitors_workspace": True,
        "expect_source": "workspace",
        "description": "产出仅落在工作区记录(非代码任务)。",
    },
    "external_dir": {
        "worktree_criterion": False,
        "monitors_product_repo": False,
        "monitors_workspace": True,
        "expect_source": "workspace",
        "description": "产出到外部目录(配置/文档任务)。",
    },
    "remote_system": {
        "worktree_criterion": False,
        "monitors_product_repo": False,
        "monitors_workspace": True,
        "expect_source": "workspace",  # 只从工作区 events 派生 expect(S12 落实3)
        "description": "产出到远端系统(部署任务)。",
    },
    "no_file_output": {
        "worktree_criterion": False,
        "monitors_product_repo": False,
        "monitors_workspace": True,
        "expect_source": "workspace",
        "description": "无文件产出(纯调研/口头结论)。",
    },
}

# 安全默认(产出位置未声明):不假设产品仓,只用与产出位置无关的判据(S8 硬约束2)。
_DEFAULT_OUTPUT_LOCATION: dict[str, Any] = {
    "worktree_criterion": False,
    "monitors_product_repo": False,
    "monitors_workspace": True,
    "expect_source": "workspace",
    "description": "UNDECLARED output location — safe default (no product-repo assumption).",
}

# 任务级 output_target → 产出位置类别的映射(S12:取值域与 304 D1 对齐)。
_OUTPUT_TARGET_TO_LOCATION: dict[str, str] = {
    "tools": "product_repo_worktree",
    "docs": "product_repo_worktree",
    "config": "product_repo_worktree",
    "remote": "remote_system",
    "workspace_only": "workspace_records",
}


def resolve_output_location(
    output_target: str | None,
    collaboration_profile: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """把任务级 output_target + 项目 collaboration_profile 解析为一个产出位置类别。

    规则(S12 落实2/补漏二):
      - output_target 能映射到 location → 用之;
      - 否则用项目 collaboration_profile.output_locations(若唯一)或退到安全默认;
      - output_target 声明 remote/workspace_only 但项目 output_locations 未含该项 →
        退到与产出位置无关的判据 + 明确提示(不报错终止)。
    """
    warns: list[str] = []
    if output_target:
        key = output_target.rstrip("/")
        loc = _OUTPUT_TARGET_TO_LOCATION.get(key)
        if loc is not None:
            # 不匹配校验:任务声明某类,但项目 output_locations 不含 → 提示并仍用该类
            # (观测面选择会因该类不用产品仓而安全退化)。
            allowed = (collaboration_profile or {}).get("output_locations") or []
            profile = OUTPUT_LOCATION_PROFILES.get(loc, _DEFAULT_OUTPUT_LOCATION)
            if allowed and not profile.get("monitors_product_repo"):
                # 非产品仓类产出位置,确认项目声明里是否真有该能力
                if loc not in allowed and loc not in (
                    "workspace_records", "no_file_output"
                ):
                    warns.append(
                        f"任务声明 output_target={output_target!r}(→ {loc}),"
                        f"但项目 collaboration_profile.output_locations={allowed} 未含该类;"
                        "已退到与产出位置无关的判据(CPU 增量 + 会话文件)。"
                    )
            return loc, warns
    # 无 output_target:尝试从项目档案取
    if collaboration_profile:
        allowed = collaboration_profile.get("output_locations") or []
        if len(allowed) == 1 and allowed[0] in OUTPUT_LOCATION_PROFILES:
            return allowed[0], warns
        if allowed:
            # 多个声明且未在任务层选定 → 安全默认 + 提示
            warns.append(
                f"任务未声明 output_target,项目 output_locations={allowed}(多个);"
                "已退到与产出位置无关的判据(CPU 增量 + 会话文件),请显式声明产出位置。"
            )
            return "__undeclared__", warns
    warns.append(
        "产出位置未声明,已退到与产出位置无关的判据(CPU 增量 + 会话文件),不假设产品仓。"
    )
    return "__undeclared__", warns


def get_output_location_profile(location_key: str) -> tuple[dict[str, Any], list[str]]:
    """返回 (profile, warnings)。未知/未声明 → 安全默认 + 明确提示。"""
    warns: list[str] = []
    profile = OUTPUT_LOCATION_PROFILES.get(location_key)
    if profile is None:
        if location_key != "__undeclared__":
            warns.append(
                f"未知产出位置 {location_key!r},已退到安全默认(不假设产品仓)。"
                " 新增产出位置请在 OUTPUT_LOCATION_PROFILES 增加一条档案(零代码改动)。"
            )
        return dict(_DEFAULT_OUTPUT_LOCATION), warns
    return dict(profile), warns


def select_observation_plan(
    runtime_type: str | None,
    output_target: str | None,
    collaboration_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """融合【运行体类型 × 产出位置】两维,产出编排层消费的观测计划(S2/S8/S12)。

    返回 dict:
      - worktree_criterion: bool   worktree 判据是否适用(非代码任务 → False)
      - stall_surfaces: list[str]  判停滞用的观测面(缓冲运行体不含 run_log)
      - run_log_role: str          "end_only" | "stall" | "none"
      - expect_source: str         "both" | "workspace" | "product_repo"
      - monitors_product_repo: bool
      - monitors_workspace: bool
      - warnings: list[str]        所有退化/默认的明确提示(不得静默)
    """
    runtime_profile, rt_warns = get_runtime_profile(runtime_type)
    location_key, loc_warns = resolve_output_location(
        output_target, collaboration_profile
    )
    loc_profile, locp_warns = get_output_location_profile(location_key)

    warnings = rt_warns + loc_warns + locp_warns

    # 停滞观测面:取运行体档案的 stall_surfaces(已按缓冲与否排除 run_log)。
    stall_surfaces = list(runtime_profile.get("stall_surfaces", []))
    # 产出位置不用产品仓时,worktree 判据关闭(S8 硬约束3)。
    worktree_criterion = bool(loc_profile.get("worktree_criterion"))
    if not worktree_criterion and "worktree" in stall_surfaces:
        # 该产出位置不改产品仓 → worktree 面对该任务无意义,从停滞面移除。
        stall_surfaces = [s for s in stall_surfaces if s != "worktree"]

    return {
        "runtime_type": runtime_type or "__unknown__",
        "runtime_profile": runtime_profile,
        "output_location": location_key,
        "output_location_profile": loc_profile,
        "worktree_criterion": worktree_criterion,
        "stall_surfaces": stall_surfaces,
        "run_log_role": runtime_profile.get("run_log_role", "end_only"),
        "expect_source": loc_profile.get("expect_source", "workspace"),
        "monitors_product_repo": bool(loc_profile.get("monitors_product_repo")),
        "monitors_workspace": bool(loc_profile.get("monitors_workspace")),
        "warnings": warnings,
    }
