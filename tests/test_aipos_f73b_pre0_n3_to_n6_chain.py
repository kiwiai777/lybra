"""AIPOS-F73B 前置零测试：推导核 N3→N6 完整链路夹具。

测试推导核 derive_next_step 在 claimed 卡状态下，按真实记录文件逐节推导：
- N2→N3: 已 return + 审计卡存在 → dispatch
- N3: 已 dispatch 无 verdict → await_artifact
- N3→N4: 已 dispatch + 审计卡有 VERDICT → audit-verdict 命令
- N4→N5: 已 verdict PASS 无 finalization → finalize
- N5→N6: 已 finalization 无 closure → close
- N6: 已 closure → 无事可做

每个测试用真实记录文件断言 current_node 和命令前缀。
"""
import json
import tempfile
from pathlib import Path

import pytest

# 从 aipos_cli.next_resolver 导入
from tools.aipos_cli.next_resolver import derive_next_step


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时治理仓结构（带记录目录）。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    
    # 创建必需目录（按 schema_loader 的路径规则）
    (ws / "5_tasks" / "queue" / "claimed").mkdir(parents=True)
    (ws / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (ws / "5_tasks" / "task_cards").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "claims").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "returns").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "audit_dispatches").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "audit_verdicts").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "finalizations").mkdir(parents=True)
    (ws / "5_tasks" / "records" / "closures").mkdir(parents=True)
    
    return ws


def _write_task_card(workspace, task_id, queue_dir="claimed", task_mode="code", audit_required=True):
    """写入任务卡到指定队列目录。"""
    card_path = workspace / "5_tasks" / "queue" / queue_dir / f"{task_id.lower()}.md"
    frontmatter = f"""---
task_id: {task_id}
task_mode: {task_mode}
audit_required: {audit_required}
assigned_to: exec.lybra.test
---

# {task_id}

测试卡。
"""
    card_path.write_text(frontmatter, encoding="utf-8")


def _write_claim_record(workspace, task_id, agent_instance="exec.lybra.test"):
    """写入认领记录。"""
    claims_dir = workspace / "5_tasks" / "records" / "claims" / task_id
    claims_dir.mkdir(parents=True, exist_ok=True)
    
    claim_file = claims_dir / "claim_20260908_000000_exec.md"
    frontmatter = f"""---
event_type: claim
task_id: {task_id}
agent_instance: {agent_instance}
actor: {agent_instance}
owner_policy_ref: policy_v1
timestamp: 2026-09-08T00:00:00Z
---

认领记录。
"""
    claim_file.write_text(frontmatter, encoding="utf-8")


def _write_return_record(workspace, task_id, result_summary="完成"):
    """写入交回记录和 RETURN.md。"""
    # 写 return 记录
    returns_dir = workspace / "5_tasks" / "records" / "returns" / task_id
    returns_dir.mkdir(parents=True, exist_ok=True)
    
    return_file = returns_dir / "return_20260908_010000_exec.md"
    frontmatter = f"""---
event_type: return
task_id: {task_id}
result_summary: {result_summary}
timestamp: 2026-09-08T01:00:00Z
---

交回记录。
"""
    return_file.write_text(frontmatter, encoding="utf-8")
    
    # 写 RETURN.md
    task_cards_dir = workspace / "5_tasks" / "task_cards" / task_id
    task_cards_dir.mkdir(parents=True, exist_ok=True)
    
    return_md = task_cards_dir / "RETURN.md"
    content = f"""---
task_id: {task_id}
returned_by: exec.lybra.test
---

# RETURN

## 一句话结论

{result_summary}

## 实现清单

完成所有交付。
"""
    return_md.write_text(content, encoding="utf-8")


def _write_audit_card(workspace, task_id, audit_id=None):
    """写入审计卡。注意：审计卡要在 queue 目录中才能被 _check_audit_card 识别。"""
    if audit_id is None:
        audit_id = f"{task_id}R"
    
    # 审计卡在 queue/claimed 中
    audit_card_path = workspace / "5_tasks" / "queue" / "claimed" / f"{audit_id.lower()}.md"
    content = f"""---
task_id: {audit_id}
reviewed_task_id: {task_id}
task_mode: audit
---

# {audit_id}

审计卡。
"""
    audit_card_path.write_text(content, encoding="utf-8")
    
    # 同时在 task_cards 中也创建（便于存放 VERDICT）
    audit_card_dir = workspace / "5_tasks" / "task_cards" / audit_id
    audit_card_dir.mkdir(parents=True, exist_ok=True)


def _write_dispatch_record(workspace, task_id, audit_id=None):
    """写入派审记录。"""
    if audit_id is None:
        audit_id = f"{task_id}R"
    
    # 注意：dispatch 记录在 audit_task_id 目录下
    dispatches_dir = workspace / "5_tasks" / "records" / "audit_dispatches" / audit_id
    dispatches_dir.mkdir(parents=True, exist_ok=True)
    
    dispatch_file = dispatches_dir / f"dispatch_{audit_id}_20260908_020000.md"
    frontmatter = f"""---
event_type: audit_dispatch
task_id: {task_id}
audit_task_id: {audit_id}
timestamp: 2026-09-08T02:00:00Z
---

派审记录。
"""
    dispatch_file.write_text(frontmatter, encoding="utf-8")


def _write_verdict_in_audit_card(workspace, task_id, audit_id=None, verdict="PASS"):
    """在审计卡的 RETURN.md 中写入 verdict。"""
    if audit_id is None:
        audit_id = f"{task_id}R"
    
    audit_card_dir = workspace / "5_tasks" / "task_cards" / audit_id
    audit_card_dir.mkdir(parents=True, exist_ok=True)
    
    verdict_md = audit_card_dir / "RETURN.md"
    content = f"""---
task_id: {audit_id}
reviewed_task_id: {task_id}
---

# VERDICT

## 一句话结论

审计通过

## verdict

{verdict}

## 审计意见

无问题。
"""
    verdict_md.write_text(content, encoding="utf-8")


def _write_verdict_record(workspace, task_id, verdict="PASS"):
    """写入裁决记录。"""
    # 注意：verdict 记录在 reviewed_task_id 目录下
    verdicts_dir = workspace / "5_tasks" / "records" / "audit_verdicts" / task_id
    verdicts_dir.mkdir(parents=True, exist_ok=True)
    
    verdict_file = verdicts_dir / "verdict_20260908_030000.md"
    frontmatter = f"""---
event_type: verdict
task_id: {task_id}
verdict: {verdict}
timestamp: 2026-09-08T03:00:00Z
---

裁决记录。
"""
    verdict_file.write_text(frontmatter, encoding="utf-8")


def _write_finalization_record(workspace, task_id):
    """写入 finalization 记录。"""
    finalizations_dir = workspace / "5_tasks" / "records" / "finalizations" / task_id
    finalizations_dir.mkdir(parents=True, exist_ok=True)
    
    finalization_file = finalizations_dir / "finalization_20260908_040000.md"
    frontmatter = f"""---
event_type: finalization
task_id: {task_id}
finalize_ref: finalize_v1
timestamp: 2026-09-08T04:00:00Z
---

Finalization 记录。
"""
    finalization_file.write_text(frontmatter, encoding="utf-8")


def _write_closure_record(workspace, task_id):
    """写入 closure 记录。"""
    closures_dir = workspace / "5_tasks" / "records" / "closures" / task_id
    closures_dir.mkdir(parents=True, exist_ok=True)
    
    closure_file = closures_dir / "closure_20260908_050000.md"
    frontmatter = f"""---
event_type: closure
task_id: {task_id}
timestamp: 2026-09-08T05:00:00Z
---

Closure 记录。
"""
    closure_file.write_text(frontmatter, encoding="utf-8")


# ============================================================================
# 测试：N2→N3 已 return + 审计卡存在 → dispatch
# ============================================================================

def test_f73b_pre0_n2_to_n3_dispatch(temp_workspace):
    """N2→N3: 已 return + 审计卡存在但未派审 → 输出 dispatch 命令。"""
    task_id = "AIPOS-TEST-N2N3"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    
    result = derive_next_step(task_id, temp_workspace)
    
    assert result["derivable"] is True
    assert result["current_node"] == "return"
    assert result["verb"] == "lybra_audit_dispatch_dry_run"
    assert "lybra audit dispatch" in result["command"]
    assert "N2→N3" in result["notes"]


# ============================================================================
# 测试：N3 已派审未裁决 → await_artifact
# ============================================================================

def test_f73b_pre0_n3_await_verdict(temp_workspace):
    """N3: 已派审但审计卡无 VERDICT → await_artifact。"""
    task_id = "AIPOS-TEST-N3"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    _write_dispatch_record(temp_workspace, task_id)
    
    result = derive_next_step(task_id, temp_workspace)
    
    assert result["derivable"] is False
    assert result["current_node"] == "audit_dispatch"
    assert "await_artifact" in result.get("action", {}).get("type", "")
    assert "N3" in result["notes"]


# ============================================================================
# 测试：N3→N4 审计卡有 VERDICT → 输出 audit-verdict 命令
# ============================================================================

def test_f73b_pre0_n3_to_n4_verdict_command(temp_workspace):
    """N3→N4: 审计卡已有 VERDICT 但无 verdict 记录 → (暂不自动提交,由顾问扣扳机)。"""
    task_id = "AIPOS-TEST-N3N4"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    _write_dispatch_record(temp_workspace, task_id)
    _write_verdict_in_audit_card(temp_workspace, task_id, verdict="PASS")
    
    # 当前逻辑: 有 dispatch 无 verdict 记录 → await (F73B 前置零不自动提交裁决)
    result = derive_next_step(task_id, temp_workspace)
    
    # 因为还没有 verdict 记录，仍处于 N3
    assert result["current_node"] == "audit_dispatch"
    assert result["derivable"] is False


# ============================================================================
# 测试：N4→N5 已 verdict PASS 无 finalization → finalize
# ============================================================================

def test_f73b_pre0_n4_to_n5_finalize(temp_workspace):
    """N4→N5: 已 verdict PASS 无 finalization → 输出 finalize 命令。"""
    task_id = "AIPOS-TEST-N4N5"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    _write_dispatch_record(temp_workspace, task_id)
    _write_verdict_record(temp_workspace, task_id, verdict="PASS")
    
    result = derive_next_step(task_id, temp_workspace)
    
    assert result["derivable"] is True
    assert result["current_node"] == "audit_verdict"
    assert result["verb"] == "lybra_finalize"
    assert "lybra finalize" in result["command"]
    assert "--push --deploy" in result["command"]
    assert "N4→N5" in result["notes"]


# ============================================================================
# 测试：N5→N6 已 finalization 无 closure → close
# ============================================================================

def test_f73b_pre0_n5_to_n6_close(temp_workspace):
    """N5→N6: 已 finalization 无 closure → 输出 close 命令。"""
    task_id = "AIPOS-TEST-N5N6"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    _write_dispatch_record(temp_workspace, task_id)
    _write_verdict_record(temp_workspace, task_id, verdict="PASS")
    _write_finalization_record(temp_workspace, task_id)
    
    result = derive_next_step(task_id, temp_workspace)
    
    assert result["derivable"] is True
    assert result["current_node"] == "finalize"
    assert result["verb"] == "lybra_queue_close_dry_run"
    assert "lybra queue close" in result["command"]
    assert "N5→N6" in result["notes"]


# ============================================================================
# 测试：N6 已 closure → 无事可做
# ============================================================================

def test_f73b_pre0_n6_closure_done(temp_workspace):
    """N6: 已 closure → 无事可做。"""
    task_id = "AIPOS-TEST-N6"
    
    _write_task_card(temp_workspace, task_id, queue_dir="claimed")
    _write_claim_record(temp_workspace, task_id)
    _write_return_record(temp_workspace, task_id)
    _write_audit_card(temp_workspace, task_id)
    _write_dispatch_record(temp_workspace, task_id)
    _write_verdict_record(temp_workspace, task_id, verdict="PASS")
    _write_finalization_record(temp_workspace, task_id)
    _write_closure_record(temp_workspace, task_id)
    
    result = derive_next_step(task_id, temp_workspace)
    
    assert result["derivable"] is False
    assert result["current_node"] == "close"
    assert "N6" in result["notes"]
    assert "已结案" in result["suggested_action"] or "无事可做" in result["notes"]
