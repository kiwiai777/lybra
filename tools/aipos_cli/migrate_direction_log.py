#!/usr/bin/env python3
"""
Migrate decision_log (or legacy direction_log) from single monthly file to directory structure.

New structure:
  governance/decision_log/<YYYY-MM>/<DD>-<seq>-<slug>.md
  governance/decision_log/<YYYY-MM>/INDEX.md

Original file is renamed to .archived (preserved byte-for-byte).
Default target is decision_log/; use --target-base to override.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple


class DirectionEntry(NamedTuple):
    """Single decision log entry."""
    date: str  # YYYY-MM-DD
    title: str
    content: str  # Full content including heading and body


# Matches: ## 2026-07-07 — Title  or  ## 2026-07-07(a) — Title
#          or ## 2026-07-09 / 2026-07-10 — Title (dual-date range)
ENTRY_HEADING_RE = re.compile(
    r"^##\s+(\d{4}-\d{2}-\d{2})(?:\s*/\s*\d{4}-\d{2}-\d{2})?(?:\([a-z]\))?\s*[—–\-]\s*(.+?)\s*$"
)

SEPARATOR_LINE = "---"


def parse_monthly_file(text: str) -> tuple[str, list[DirectionEntry]]:
    """
    Parse a single monthly decision_log file.
    
    Returns:
        (preamble, entries): preamble is the header before first entry.
    """
    lines = text.splitlines(keepends=True)
    preamble_lines: list[str] = []
    entries: list[DirectionEntry] = []
    current_date: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []
    
    in_preamble = True
    
    for line in lines:
        match = ENTRY_HEADING_RE.match(line.rstrip())
        if match:
            # Save previous entry if any
            if current_date and current_title:
                entries.append(DirectionEntry(
                    date=current_date,
                    title=current_title,
                    content="".join(current_lines)
                ))
            # Start new entry
            in_preamble = False
            current_date = match.group(1)
            current_title = match.group(2)
            current_lines = [line]
        else:
            if in_preamble:
                preamble_lines.append(line)
            else:
                current_lines.append(line)
    
    # Save last entry
    if current_date and current_title:
        entries.append(DirectionEntry(
            date=current_date,
            title=current_title,
            content="".join(current_lines)
        ))
    
    return "".join(preamble_lines), entries


def slugify(title: str, max_len: int = 50) -> str:
    """Convert title to filesystem-safe slug."""
    # Remove special chars, keep alphanumeric and spaces
    clean = re.sub(r'[^\w\s\-]', '', title.lower())
    # Convert spaces and multiple dashes to single dash
    slug = re.sub(r'[-\s]+', '-', clean).strip('-')
    # Truncate
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip('-')
    return slug or "entry"


def generate_index_entry(entry: DirectionEntry, filename: str) -> str:
    """Generate one line for INDEX.md."""
    return f"- [{entry.date}](./{filename}) — {entry.title}\n"


def migrate_file(
    source_path: Path,
    target_base: Path,
    dry_run: bool = False
) -> dict[str, any]:
    """
    Migrate one monthly decision_log file.
    
    Returns summary dict with stats and file list.
    """
    if not source_path.is_file():
        return {"error": f"Source not found: {source_path}"}
    
    text = source_path.read_text(encoding="utf-8")
    preamble, entries = parse_monthly_file(text)
    
    if not entries:
        return {"error": "No entries found in source file"}
    
    # Determine YYYY-MM from first entry
    first_date = entries[0].date
    year_month = first_date[:7]  # YYYY-MM
    
    target_dir = target_base / year_month
    actions: list[str] = []
    
    # Group entries by date for sequence numbering
    date_counts: dict[str, int] = {}
    
    for entry in entries:
        day = entry.date[8:10]  # DD
        seq = date_counts.get(day, 0) + 1
        date_counts[day] = seq
        
        slug = slugify(entry.title)
        filename = f"{day}-{seq:02d}-{slug}.md"
        target_path = target_dir / filename
        
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path.write_text(entry.content, encoding="utf-8")
        
        actions.append(f"CREATE {target_path.relative_to(target_base)}")
    
    # Generate INDEX.md
    index_lines = [f"# Direction Log Index — {year_month}\n\n"]
    date_counts_for_index: dict[str, int] = {}
    
    for entry in entries:
        day = entry.date[8:10]
        seq = date_counts_for_index.get(day, 0) + 1
        date_counts_for_index[day] = seq
        
        slug = slugify(entry.title)
        filename = f"{day}-{seq:02d}-{slug}.md"
        index_lines.append(generate_index_entry(entry, filename))
    
    index_path = target_dir / "INDEX.md"
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        index_path.write_text("".join(index_lines), encoding="utf-8")
    
    actions.append(f"CREATE {index_path.relative_to(target_base)}")
    
    # Archive original
    archived_path = source_path.with_suffix(source_path.suffix + ".archived")
    if not dry_run:
        source_path.rename(archived_path)
    
    actions.append(f"RENAME {source_path.name} → {archived_path.name}")
    
    return {
        "source": str(source_path),
        "year_month": year_month,
        "entries_count": len(entries),
        "actions": actions,
        "dry_run": dry_run
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate decision_log to directory structure")
    parser.add_argument("source", type=Path, help="Source monthly .md file")
    parser.add_argument("--target-base", type=Path, 
                        help="Target decision_log base (default: governance/decision_log relative to source)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    
    args = parser.parse_args()
    
    source = args.source.resolve()
    if args.target_base:
        target_base = args.target_base.resolve()
    else:
        # Default: governance/decision_log/ (new standard naming)
        # Infer governance/ from source path
        if "governance" in source.parts:
            gov_idx = source.parts.index("governance")
            gov_path = Path(*source.parts[:gov_idx+1])
            target_base = gov_path / "decision_log"
        else:
            # Fallback: same directory as source
            target_base = source.parent
    
    result = migrate_file(source, target_base, dry_run=args.dry_run)
    
    if "error" in result:
        print(f"ERROR: {result['error']}", file=sys.stderr)
        sys.exit(1)
    
    print(f"{'[DRY RUN] ' if result['dry_run'] else ''}Migrated {result['entries_count']} entries")
    print(f"Target: {result['year_month']}/")
    print("\nActions:")
    for action in result["actions"]:
        print(f"  {action}")
    
    if result["dry_run"]:
        print("\nRun without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
