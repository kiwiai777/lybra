"""Typed enum constants generated from enums.schema.json via schema_loader.

AIPOS-R6B: Single-source typed constants for record_type and verdict values.
All comparison/assignment/write paths in tools/ must use these constants
instead of string literals. Zero behavior change: each constant IS the
original string (str subclass / plain str), so == comparisons with data
from files/network remain valid.

一机制一实现: constants come from schema_loader (which reads enums.schema.json).
No second enum mapping. If the schema changes, constants auto-update.
"""
from __future__ import annotations

from tools.schema_loader import get_enum_values


def _build_namespace(enum_name: str) -> dict[str, str]:
    """Build a namespace dict from an enum's schema values.

    Each value becomes an UPPER_CASE attribute mapping to the original string.
    E.g., record_type "claim" -> CLAIM = "claim"
          verdict "PASS" -> PASS = "PASS"
    """
    values = get_enum_values(enum_name)
    return {v.upper(): v for v in values}


# ---------------------------------------------------------------------------
# RecordType: constants for record_type field values
# Usage: RecordType.CLAIM, RecordType.SESSION_RECORD, etc.
# ---------------------------------------------------------------------------
class RecordType:
    """Record type constants from enums.schema.json record_type enum.

    All values are plain strings matching the schema exactly.
    """
    pass


# Populate RecordType from schema
for _attr, _val in _build_namespace("record_type").items():
    setattr(RecordType, _attr, _val)


# ---------------------------------------------------------------------------
# Verdict: constants for verdict field values
# Usage: Verdict.PASS, Verdict.FAIL, Verdict.BLOCK, etc.
# ---------------------------------------------------------------------------
class Verdict:
    """Verdict constants from enums.schema.json verdict enum.

    All values are plain strings matching the schema exactly.
    """
    pass


# Populate Verdict from schema
for _attr, _val in _build_namespace("verdict").items():
    setattr(Verdict, _attr, _val)


# ---------------------------------------------------------------------------
# Cleanup module-level temporaries
# ---------------------------------------------------------------------------
del _attr, _val, _build_namespace


__all__ = ["RecordType", "Verdict"]
