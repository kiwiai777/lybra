#!/usr/bin/env python3
"""AIPOS-R6H 靶④: 治理仓清理脚本 — 去除connection.json重复条目和审计误enroll残迹

运行位置: 治理仓 ai-project-os/2_projects/lybra
功能:
1. connection.json 去重: 每个 agent_instance 只保留一条 token
2. 删除残迹: .lybra/role 和 .lybra/actor (审计误enroll落治理仓的遗留)
"""
import json
import sys
from pathlib import Path


def deduplicate_tokens(connection_file: Path) -> dict:
    """去重 connection.json 的 tokens 数组"""
    data = json.loads(connection_file.read_text(encoding="utf-8"))
    tokens = data.get("tokens", [])
    
    # 按 agent_instance 或 role 去重（保留最后一个）
    seen = {}
    deduped = []
    
    for token_entry in tokens:
        # 优先按 agent_instance 去重
        agent_instance = token_entry.get("agent_instance")
        role = token_entry.get("role")
        
        if agent_instance:
            key = ("instance", agent_instance)
        elif role:
            key = ("role", role)
        else:
            # 没有 instance 也没有 role，跳过
            continue
        
        if key in seen:
            # 重复，记录被替换的
            print(f"  Duplicate found: {key}, replacing previous entry", file=sys.stderr)
        
        seen[key] = token_entry
    
    # 重建去重后的列表（保持原顺序，但每个key只保留最后一次出现）
    deduped = list(seen.values())
    
    data["tokens"] = deduped
    
    return data


def main() -> int:
    # 假设运行在治理仓根目录
    governance_root = Path.cwd()
    lybra_dir = governance_root / ".lybra"
    
    if not lybra_dir.exists():
        print(f"Error: .lybra directory not found in {governance_root}", file=sys.stderr)
        print("Are you running this in the governance repo root?", file=sys.stderr)
        return 1
    
    connection_file = lybra_dir / "connection.json"
    if not connection_file.exists():
        print(f"Error: {connection_file} not found", file=sys.stderr)
        return 1
    
    print(f"Cleaning governance repo: {governance_root}")
    print()
    
    # 1. 去重 connection.json
    print("1. Deduplicating connection.json tokens...")
    original_data = json.loads(connection_file.read_text(encoding="utf-8"))
    original_count = len(original_data.get("tokens", []))
    
    deduped_data = deduplicate_tokens(connection_file)
    deduped_count = len(deduped_data.get("tokens", []))
    
    if original_count != deduped_count:
        print(f"   Removed {original_count - deduped_count} duplicate token(s)")
        print(f"   Before: {original_count} tokens, After: {deduped_count} tokens")
        
        # 备份原文件
        backup_file = connection_file.with_suffix(".json.backup")
        backup_file.write_text(json.dumps(original_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"   Backup saved to: {backup_file}")
        
        # 写入去重后的文件
        connection_file.write_text(json.dumps(deduped_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        connection_file.chmod(0o600)
        print(f"   ✓ Updated: {connection_file}")
    else:
        print(f"   No duplicates found ({original_count} tokens)")
    
    print()
    
    # 2. 删除残迹文件
    print("2. Removing audit-enroll residue files...")
    residue_files = ["role", "actor", "policy"]
    removed = []
    
    for fname in residue_files:
        fpath = lybra_dir / fname
        if fpath.exists():
            # 备份
            backup = fpath.with_suffix(fpath.suffix + ".removed")
            fpath.rename(backup)
            removed.append(fname)
            print(f"   Removed: {fname} (backed up to {backup.name})")
    
    if not removed:
        print("   No residue files found")
    else:
        print(f"   ✓ Removed {len(removed)} residue file(s)")
    
    print()
    print("✓ Governance repo cleanup complete!")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
