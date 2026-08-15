"""CLI self-describing error messages (AIPOS-R6L 大项C).

从 schema/verbs.schema.json 读取动词定义，生成带完整参数shape和可抄示例的报错信息。
禁手写第二份参数定义——与MCP面同源。
"""

from pathlib import Path
from typing import Any
from tools.schema_loader import get_verb_contract


def generate_verb_help(verb_name: str, repo_root: Path | None = None) -> str:
    """生成动词的完整帮助信息，包含必填参数清单和可抄示例。
    
    Args:
        verb_name: MCP动词名（如 "lybra_queue_return"）
        repo_root: 仓库根路径
        
    Returns:
        格式化的帮助字符串
    """
    contract = get_verb_contract(verb_name, repo_root)
    if not contract:
        return f"动词 {verb_name} 未在 verbs.schema.json 中定义"
    
    lines = []
    lines.append(f"\n动词: {verb_name}")
    lines.append(f"描述: {contract.get('description', '(无描述)')}")
    lines.append(f"阶段: {', '.join(contract.get('phases', []))}")
    
    # dry_run 参数
    dry_run_params = contract.get("dry_run_parameters", {})
    if dry_run_params:
        lines.append("\n必填参数（dry_run阶段）:")
        for param_name, param_def in dry_run_params.items():
            required = param_def.get("required", False)
            param_type = param_def.get("type", "string")
            desc = param_def.get("description", "")
            req_marker = "【必填】" if required else "【可选】"
            lines.append(f"  {req_marker} {param_name} ({param_type}): {desc}")
    
    # 生成可抄示例
    lines.append("\n可抄最小示例:")
    example = _generate_example(verb_name, dry_run_params)
    lines.append(example)
    
    # 下一步指引
    lines.append("\n下一步:")
    lines.append(f"1. 调用 {verb_name}_dry_run 预览操作")
    lines.append(f"2. 如果 verdict != BLOCK，使用返回的 dry_run_token 调用 {verb_name}_confirm 确认执行")
    
    return "\n".join(lines)


def _generate_example(verb_name: str, params: dict[str, Any]) -> str:
    """根据参数定义生成示例。"""
    
    # 预定义的示例值
    examples = {
        "lybra_queue_return": {
            "task_id": "AIPOS-R6L",
            "actor": "exec.lybra.kiwiai-dev",
            "agent_instance": "exec.lybra.kiwiai-dev",
            "autonomy_mode": "Supervised",
            "owner_policy_ref": "pol_lybra_dev_9",
            "result_summary": "任务完成描述",
            "artifact_refs": '["task_cards/TASK-ID/RETURN.md"]',
            "completion_report_ref": "task_cards/TASK-ID/RETURN.md",
        },
        "lybra_finalize": {
            "task_id": "AIPOS-R6L",
            "actor": "owner.lybra.kiwiai-dev",
            "deployment_strategy": "push_and_deploy",
        },
        "lybra_queue_close": {
            "task_id": "AIPOS-R6L",
            "actor": "owner.lybra.kiwiai-dev",
            "closure_evidence": "任务已完成并验证",
        },
        "lybra_mark_concluded": {
            "task_id": "AIPOS-R6L",
            "actor": "owner.lybra.kiwiai-dev",
            "conclusion_reason": "任务结论描述",
        },
    }
    
    if verb_name in examples:
        example_data = examples[verb_name]
        lines = [f"  {verb_name}_dry_run("]
        for key, value in example_data.items():
            if isinstance(value, str) and not value.startswith('["'):
                lines.append(f'    {key}="{value}",')
            else:
                lines.append(f"    {key}={value},")
        lines.append("  )")
        return "\n".join(lines)
    
    # 默认生成
    lines = [f"  {verb_name}_dry_run("]
    for param_name, param_def in params.items():
        if param_def.get("required", False):
            param_type = param_def.get("type", "string")
            if param_type == "string":
                lines.append(f'    {param_name}="<填写值>",')
            elif param_type == "array":
                lines.append(f'    {param_name}=["<项目1>", "<项目2>"],')
            else:
                lines.append(f'    {param_name}=<填写{param_type}>,')
    lines.append("  )")
    return "\n".join(lines)


def wrap_error_with_verb_help(error_msg: str, verb_name: str, repo_root: Path | None = None) -> str:
    """包装错误信息，附加动词帮助。
    
    如果动词在schema中未定义，提供通用错误指引而不是失败。
    """
    try:
        help_text = generate_verb_help(verb_name, repo_root)
    except Exception:
        # 动词未在schema中定义，提供通用帮助
        help_text = f"\n动词 {verb_name} 的详细帮助信息不可用。\n请检查参数是否完整，或参考文档。"
    
    return f"{error_msg}\n{help_text}"
