#!/usr/bin/env python3
"""
AIPOS-R6A FIX2 诊断：PreAuthorized envelope匹配失败排查
测试pol_lybra_dev_9匹配AIPOS-FND-16卡的完整流程
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

from tools.aipos_cli.autonomy_policy import (
    load_policy,
    match_claim_envelope,
    count_preauthorized_claims,
)
from tools.aipos_cli.board_adapter import load_task_snapshot

def diagnose_preauth_match():
    repo_root = Path("/home/kiwi/ai-project-os/2_projects/lybra")
    
    # 测试参数
    owner_policy_ref = "pol_lybra_dev_9"
    task_id = "AIPOS-FND-16"
    canonical_agent_instance = "exec.lybra.kiwiai-dev"
    actor = "exec.lybra.kiwiai-dev"
    claiming_role = "executor"  # 从token读取
    
    print("=" * 60)
    print("PreAuthorized Envelope匹配诊断")
    print("=" * 60)
    
    # 步骤1: 加载policy
    print(f"\n[1] 加载policy: {owner_policy_ref}")
    policy = load_policy(repo_root, owner_policy_ref)
    if policy is None:
        print(f"❌ FAIL: policy不存在")
        return 1
    
    print(f"✅ Policy加载成功")
    print(f"  policy_id: {policy.get('policy_id')}")
    print(f"  mode: {policy.get('mode')}")
    print(f"  status: {policy.get('status')}")
    print(f"  approved_by_owner: {policy.get('approved_by_owner')}")
    print(f"  agent_or_role: {policy.get('agent_or_role')}")
    print(f"  active_from: {policy.get('active_from')}")
    print(f"  expires_at: {policy.get('expires_at')}")
    print(f"  max_tasks: {policy.get('max_tasks')}")
    print(f"  task_selector_task_mode: {policy.get('task_selector_task_mode')}")
    print(f"  task_selector_project: {policy.get('task_selector_project')}")
    
    # 步骤2: 加载task snapshot
    print(f"\n[2] 加载task snapshot: {task_id}")
    snapshot = load_task_snapshot(repo_root, task_id=task_id, path=None)
    if snapshot is None:
        print(f"❌ FAIL: task不存在")
        return 1
    
    print(f"✅ Task加载成功")
    print(f"  task_id: {snapshot.get('task_id')}")
    print(f"  task_mode: {snapshot.get('task_mode')}")
    print(f"  project: {snapshot.get('project')}")
    print(f"  queue_state: {snapshot.get('queue_state')}")
    
    # 步骤3: 计数已released claims
    print(f"\n[3] 计数已released claims for policy: {owner_policy_ref}")
    released_count = count_preauthorized_claims(repo_root, owner_policy_ref)
    print(f"  released_count: {released_count}")
    
    # 步骤4: 逐条件匹配
    print(f"\n[4] 逐条件匹配")
    now = datetime.now(timezone.utc)
    
    task_mode = str(snapshot.get("task_mode") or "")
    project = str(snapshot.get("project") or "")
    
    matched, reason = match_claim_envelope(
        policy=policy,
        task_id=task_id,
        task_mode=task_mode,
        project=project,
        agent_instance=canonical_agent_instance,
        actor=actor,
        now=now,
        released_count=released_count,
        claiming_role=claiming_role,
    )
    
    if matched:
        print(f"✅ MATCHED: {reason}")
        return 0
    else:
        print(f"❌ NOT MATCHED: {reason}")
        
        # 详细诊断每个条件
        print(f"\n[5] 详细诊断")
        
        # mode
        if policy.get("mode") != "PreAuthorized":
            print(f"  ❌ mode: {policy.get('mode')} != PreAuthorized")
        else:
            print(f"  ✅ mode: PreAuthorized")
        
        # status
        if policy.get("status") != "active":
            print(f"  ❌ status: {policy.get('status')} != active")
        else:
            print(f"  ✅ status: active")
        
        # approved_by_owner
        if not policy.get("approved_by_owner"):
            print(f"  ❌ approved_by_owner: False")
        else:
            print(f"  ✅ approved_by_owner: True")
        
        # agent_or_role
        covered = str(policy.get("agent_or_role") or "").strip()
        identity = {canonical_agent_instance, actor}
        if claiming_role:
            identity.add(claiming_role)
        identity.discard("")
        if covered not in identity:
            print(f"  ❌ agent_or_role: {covered} not in {identity}")
        else:
            print(f"  ✅ agent_or_role: {covered} in {identity}")
        
        # task_mode
        sel_mode = str(policy.get("task_selector_task_mode") or "").strip()
        if sel_mode and task_mode != sel_mode:
            print(f"  ❌ task_mode: {task_mode} != {sel_mode}")
        else:
            print(f"  ✅ task_mode: {task_mode} == {sel_mode}")
        
        # project
        sel_project = str(policy.get("task_selector_project") or "").strip()
        if sel_project and project != sel_project:
            print(f"  ❌ project: {project} != {sel_project}")
        else:
            print(f"  ✅ project: {project} == {sel_project}")
        
        # max_tasks
        max_tasks = int(policy.get("max_tasks") or 0)
        if max_tasks <= 0:
            print(f"  ❌ max_tasks: {max_tasks} <= 0")
        elif released_count >= max_tasks:
            print(f"  ❌ count bound: {released_count} >= {max_tasks}")
        else:
            print(f"  ✅ count bound: {released_count} < {max_tasks}")
        
        return 1

if __name__ == "__main__":
    sys.exit(diagnose_preauth_match())
