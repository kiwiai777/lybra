"""AIPOS-340F3 测试 — claimed 中途态规则(续跑/等待执行/等待审计派生)。

用 infer_next_action 直接测规则逻辑(纯单元,不依赖文件系统)。
- 验收断言1:以 339 当前真实状态为夹具 → resume 轮派工
- 验收断言2:三种组合各有测试;既有规则零回归(见 test_basic / 340f1 套件)

注:state_reader 当前只呈现 event_type,mask 了 launch_check 通道的 event_kind
(launch_failed/blocked 在 live 下经 state_reader 变成 type=None)——故 live
`lybra turn-advancer next AIPOS-339` 暂不会命中 resume(需 state_reader 补 event_kind,
属本卡红线 rules.py+测试 之外)。本套件用忠实还原 339 真实事件 kind 的夹具验证规则正确性。
"""
from datetime import datetime, timedelta, timezone

from tools.turn_advancer.rules import infer_next_action


def _state(
    task_id="AIPOS-339",
    queue_status="claimed",
    *,
    has_return=False,
    latest_return=None,
    latest_verdict=None,
    has_audit_card=False,
    events=None,
    task_mode="code",
    audit="required",
):
    return {
        "task_id": task_id,
        "queue_status": queue_status,
        "task_frontmatter": {"task_mode": task_mode, "audit": audit},
        "latest_claim": {"canonical_agent_instance": "exec.lybra.kiwiai-dev", "claim_id": "claim_x"},
        "latest_return": latest_return,
        "latest_verdict": latest_verdict,
        "has_return_artifact": has_return,
        "has_audit_card": has_audit_card,
        "events": events or [],
    }


def test_339_real_state_fixture_yields_resume():
    """验收断言1:339 真实状态为夹具 → next = resume 轮派工。

    339 真实状态(governance_ref):claimed + events 含 started/launch_failed/blocked + 无 return。
    最新事件为失败(launch_failed/blocked ≥ 最新 started,无活跃执行)→ resume_round。
    """
    events_339 = [
        {"type": "started", "timestamp": "2026-08-05T04:44:30Z", "actor": "exec.lybra.kiwiai-dev"},
        {"type": "launch_failed", "timestamp": "2026-08-05T04:45:11Z", "reason": "silent_hang"},
        {"type": "started", "timestamp": "2026-08-05T04:46:01Z"},
        {"type": "launch_failed", "timestamp": "2026-08-05T04:46:51Z", "reason": "insufficient_activity"},
        {"type": "blocked", "timestamp": "2026-08-05T04:46:51Z", "reason": "max_launch_attempts_exceeded"},
    ]
    result = infer_next_action(_state(events=events_339))
    assert result["action"] == "resume_round"
    assert result["requires_human_judgment"] is False
    assert "resume" in result["rule"]
    assert "pump run --round-type resume" in result["rule"]


def test_claimed_no_return_recent_started_waits_executor():
    """rule 2:claimed+无return+近期started且无blocked → wait_executor(执行中,不动作)。"""
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=5)).isoformat()
    result = infer_next_action(_state(events=[{"type": "started", "timestamp": recent}]))
    assert result["action"] == "wait_executor"
    assert result["requires_human_judgment"] is False


def test_claimed_no_return_recent_started_after_old_failure_waits():
    """失败后又有近期 started(执行体重启)→ wait_executor,非 resume(只读事实,不连坐旧失败)。"""
    now = datetime.now(timezone.utc)
    old_fail = (now - timedelta(hours=2)).isoformat()
    recent_start = (now - timedelta(minutes=3)).isoformat()
    events = [
        {"type": "launch_failed", "timestamp": old_fail, "reason": "silent_hang"},
        {"type": "started", "timestamp": recent_start},
    ]
    result = infer_next_action(_state(events=events))
    assert result["action"] == "wait_executor"


def test_claimed_no_return_stale_started_no_failure_waits_human():
    """无失败信号且 started 非近期 → 事实不足,留人(不判活;不可自动续跑)。"""
    stale = "2026-08-05T03:00:00Z"  # 远早于阈值
    result = infer_next_action(_state(events=[{"type": "started", "timestamp": stale}]))
    assert result["action"] == "wait_human"
    assert result["requires_human_judgment"] is True


def test_claimed_return_no_verdict_waits_audit_derivation():
    """rule 3:claimed+有return+无裁决+审计卡未生成 → 等待审计派生(与既有审计环衔接)。"""
    result = infer_next_action(
        _state(
            has_return=True,
            latest_return={"executor_status": "completed"},
            has_audit_card=False,
            task_mode="code",
            audit="required",
        )
    )
    assert result["action"] == "wait_human"
    assert result["requires_human_judgment"] is True
    assert "审计卡" in result["rule"]


def test_claimed_return_no_verdict_dispatches_audit():
    """rule 3:claimed+有return+无裁决+审计卡已生成 → 派审计轮。"""
    result = infer_next_action(
        _state(
            has_return=True,
            latest_return={"executor_status": "completed"},
            has_audit_card=True,
            task_mode="code",
            audit="required",
        )
    )
    assert result["action"] == "dispatch_audit"
    assert result["requires_human_judgment"] is False


# --- 既有规则零回归 ---
def test_regression_pending_claims():
    result = infer_next_action(_state(queue_status="pending"))
    assert result["action"] == "claim_task"


def test_regression_completed_done():
    result = infer_next_action(_state(queue_status="completed"))
    assert result["action"] == "done"


def test_regression_claimed_return_artifact_not_returned():
    """claimed + RETURN.md 存在 + 未 return → return_work(既有 rule 2)。"""
    result = infer_next_action(_state(has_return=True, latest_return=None))
    assert result["action"] == "return_work"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
