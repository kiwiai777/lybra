# Direction Log Migration Tool

## Overview

This tool migrates `direction_log` from single monthly files to a directory structure for better maintainability.

**New structure:**
```
governance/direction_log/
├── 2026-07/
│   ├── INDEX.md
│   ├── 07-01-agent-连接器进入-v10-required.md
│   ├── 09-01-planner-改道byo-外接-planner-agent.md
│   └── ...
└── 2026-08/
    ├── INDEX.md
    └── ...
```

**Key principles:**
- One entry per file: `<DD>-<seq>-<slug>.md`
- Monthly INDEX.md for quick overview
- Original file preserved as `.archived` (byte-for-byte)
- Board parsing supports both old and new structures

## Usage

### Dry run (preview only)
```bash
python3 tools/aipos_cli/migrate_direction_log.py \
  governance/direction_log/2026-07-direction-decisions.md \
  --dry-run
```

### Actual migration
```bash
python3 tools/aipos_cli/migrate_direction_log.py \
  governance/direction_log/2026-07-direction-decisions.md
```

### With custom target
```bash
python3 tools/aipos_cli/migrate_direction_log.py \
  /path/to/2026-07-direction-decisions.md \
  --target-base /path/to/direction_log
```

## What it does

1. **Parses** the monthly file, extracting entries by `## YYYY-MM-DD — Title` headings
2. **Creates** `<YYYY-MM>/` directory
3. **Writes** individual entry files named `<DD>-<seq>-<slug>.md`
4. **Generates** `INDEX.md` with links to all entries
5. **Archives** original file by renaming to `.archived`

## Entry file naming

- `<DD>`: Day (01-31)
- `<seq>`: Sequence number (01, 02, ...) for multiple entries on same day
- `<slug>`: Filesystem-safe slug derived from title (max 50 chars)

Example: `09-02-agent-连接器交付形态homerail-式-skills-目录.md`

## Board parsing compatibility

The `project_map.py` board parser now supports both structures:

- **New structure**: Reads individual files from `<YYYY-MM>/` directories
- **Old structure**: Falls back to scanning monthly aggregate files
- **Backward compatible**: Existing tests pass unchanged

## Testing

Run tests:
```bash
pytest tools/aipos_cli/tests/test_direction_log_migration.py -v
```

Coverage:
- Migration tool (slugify, parse, dry-run, real write)
- Board parsing (old structure, new structure, multi-month, date suffixes)
- Backward compatibility (existing project_map tests)

## Template integration

New projects initialized via `workspace_init` will include:
- `governance/direction_log/.gitkeep` with structure documentation
- Ready for month-by-month organization

## Migration checklist

For migrating an existing project:

1. ✅ Backup current `direction_log/` directory
2. ✅ Run migration tool with `--dry-run` to preview
3. ✅ Run actual migration (original preserved as `.archived`)
4. ✅ Verify board still displays "最近方向" correctly
5. ✅ Keep `.archived` files for reference (can be removed after verification)

## Notes

- Content is preserved byte-for-byte (no reformatting)
- Date suffixes like `(a)`, `(b)` are supported (AIPOS-275 compatibility)
- INDEX.md is append-only (generate once, then maintain manually or regenerate)
- Multiple entries on same day get sequence numbers (01, 02, ...)
