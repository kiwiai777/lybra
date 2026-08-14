#!/usr/bin/env python3
"""
诊断 PreAuthorized claim 的完整匹配流程
模拟 MCP gate 的 _match_claim_envelope 逻辑
"""
import sys
sys.path.insert(0, 'tools')

from pathlib import Path
from datetime import datetime, timezone
from aipos_cli.autonomy_policy import load_policy, match_claim_envelope, count_preauthorized_claims
from aipos_cli.board_adapter import load_task_snapshot

gov_root = Path('/home/kiwi/ai-project-os/2_projects/lybra')

# 模拟参数
owner_policy_ref = 'pol_lybra_dev_9'
task_id = 'AIPOS-345'
canonical_agent_instance = 'exec.lybra.kiwiai-dev'
actor = 'exec.lybra.kiwiai-dev'
claiming_role = 'executor'

print("=== PreAuthorized Claim Diagnostic ===\n")

# Step 1: Load policy
print("Step 1: Load policy")
policy = load_policy(gov_root, owner_policy_ref)
if policy is None:
    print(f"❌ Policy not found: {owner_policy_ref}")
    sys.exit(1)
print(f"✅ Policy loaded: {policy.get('policy_id')}")
print(f"   mode: {policy.get('mode')}")
print(f"   status: {policy.get('status')}")

# Step 2: Load task snapshot
print(f"\nStep 2: Load task snapshot")
snapshot = load_task_snapshot(gov_root, task_id=task_id)
if snapshot is None:
    print(f"❌ Task not found: {task_id}")
    sys.exit(1)
print(f"✅ Task loaded: {snapshot.get('task_id')}")
print(f"   task_mode: {snapshot.get('task_mode')}")
print(f"   queue_state: {snapshot.get('queue_state')}")
print(f"   project: {snapshot.get('project')}")

# Step 3: Check queue_state
print(f"\nStep 3: Check queue_state")
if snapshot.get('queue_state') != 'pending':
    print(f"❌ Task is not pending (state={snapshot.get('queue_state')})")
    print("   PreAuthorized only covers pending tasks")
    sys.exit(1)
print(f"✅ Task is pending")

# Step 4: Count released claims
print(f"\nStep 4: Count released claims")
released_count = count_preauthorized_claims(gov_root, owner_policy_ref)
print(f"✅ Released count: {released_count}")

# Step 5: Match envelope
print(f"\nStep 5: Match claim envelope")
now = datetime.now(timezone.utc)
matched, reason = match_claim_envelope(
    policy=policy,
    task_id=task_id,
    task_mode=snapshot.get('task_mode', ''),
    project=snapshot.get('project', ''),
    agent_instance=canonical_agent_instance,
    actor=actor,
    now=now,
    released_count=released_count,
    claiming_role=claiming_role,
)

print(f"Result: {'✅ MATCHED' if matched else '❌ NOT MATCHED'}")
print(f"Reason: {reason}")

# Step 6: Simulate token binding check (在实际gate中)
print(f"\nStep 6: Token binding check (simulated)")
print(f"   Token agent_instance: {canonical_agent_instance}")
print(f"   Claim agent_instance: {canonical_agent_instance}")
print(f"   Token role: {claiming_role}")
print(f"   ✅ Binding matches")

# Final result
print(f"\n{'='*60}")
if matched:
    print("✅ PreAuthorized claim should auto-release!")
    print(f"   Policy: {owner_policy_ref}")
    print(f"   Task: {task_id}")
    print(f"   Agent: {canonical_agent_instance}")
    print(f"   Released: {released_count}/{policy.get('max_tasks')}")
else:
    print("❌ PreAuthorized claim would fall back to Supervised")
    print(f"   Reason: {reason}")
