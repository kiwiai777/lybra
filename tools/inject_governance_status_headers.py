#!/usr/bin/env python3
"""AIPOS-R6M 大项C①: 扫描 governance/ 全部 .md，无状态头者批量注入 status:active 默认头并出清单报告

Usage:
    python3 tools/inject_governance_status_headers.py [--repo-root PATH] [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def has_status_header(file_path: Path) -> bool:
    """检查文件是否已有 frontmatter 状态头"""
    try:
        content = file_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        
        # 检查是否有 frontmatter
        if not lines or lines[0].strip() != "---":
            return False
        
        # 查找第二个 ---
        in_frontmatter = False
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                # 提取 frontmatter
                frontmatter_lines = lines[1:i]
                # 检查是否有 status 字段
                for fm_line in frontmatter_lines:
                    if fm_line.strip().startswith("status:"):
                        return True
                return False
        
        return False
    except Exception:
        return False


def inject_status_header(file_path: Path, dry_run: bool = True) -> bool:
    """为文件注入默认状态头"""
    try:
        content = file_path.read_text(encoding="utf-8")
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        # 生成默认状态头
        default_header = f"""---
status: active
decided_at: {timestamp}
superseded_by: null
injected_by: AIPOS-R6M-auto-inject
---

"""
        
        # 如果文件已有 frontmatter，在前面插入；否则直接在开头添加
        if content.strip().startswith("---"):
            # 已有 frontmatter，跳过（不应该走到这里）
            return False
        
        new_content = default_header + content
        
        if not dry_run:
            file_path.write_text(new_content, encoding="utf-8")
        
        return True
    except Exception as e:
        print(f"  Error injecting header for {file_path}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Inject status headers to governance docs")
    parser.add_argument("--repo-root", type=str, default="/home/kiwi/ai-project-os/2_projects/lybra",
                        help="Governance repository root path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry run mode (no files modified)")
    args = parser.parse_args()
    
    repo_root = Path(args.repo_root)
    governance_dir = repo_root / "governance"
    
    if not governance_dir.is_dir():
        print(f"❌ Governance directory not found: {governance_dir}", file=sys.stderr)
        return 1
    
    print(f"🔍 AIPOS-R6M 大项C①: Scanning governance docs in {governance_dir}")
    print(f"   Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()
    
    # 扫描所有 .md 文件
    md_files = list(governance_dir.rglob("*.md"))
    
    # 过滤：排除 decision_log/ 目录（那些是指针条目，格式不同）
    md_files = [f for f in md_files if "decision_log" not in f.parts]
    
    files_without_header = []
    files_injected = []
    
    for md_file in md_files:
        rel_path = md_file.relative_to(repo_root)
        if not has_status_header(md_file):
            files_without_header.append(rel_path)
            if inject_status_header(md_file, dry_run=args.dry_run):
                files_injected.append(rel_path)
    
    # 输出报告
    print(f"📊 Scan Results:")
    print(f"   Total .md files scanned: {len(md_files)}")
    print(f"   Files without status header: {len(files_without_header)}")
    print(f"   Files {'would be' if args.dry_run else ''} injected: {len(files_injected)}")
    print()
    
    if files_without_header:
        print("Files without status header:")
        for f in files_without_header:
            status = "✅ injected" if f in files_injected else "⚠️  skipped"
            print(f"  - {f} ({status})")
        print()
    
    if args.dry_run and files_injected:
        print("⚠️  DRY-RUN mode: No files were modified.")
        print("   Run without --dry-run to apply changes.")
    elif files_injected:
        print(f"✅ Injected status headers to {len(files_injected)} files.")
    else:
        print("✅ All governance docs already have status headers.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
