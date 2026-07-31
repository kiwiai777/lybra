"""AIPOS-262B: project milestone map read surface (Owner read face).

Reads the governance declaration file ``governance/project-map.md`` and the
``governance/direction_log/`` directory, returning a structured, chart-ready
payload for the workspace page's topmost milestone-map region.

Design notes
------------
- **Zero new dependencies.** The gate core is stdlib-only and PyYAML is NOT a
  runtime dependency. ``parse_markdown_frontmatter``'s stdlib fallback parser
  intentionally rejects sequences-of-mappings / depth>=2 (contract-tested).
  ``project-map.md`` carries exactly such nested structures (``milestones`` is a
  list of maps with ``refs`` lists; ``portal`` is a nested map with a nested
  ``workers`` list). This module therefore ships a **schema-targeted** parser
  for the documented frontmatter shape (see the file header in
  ``project-map.md``) rather than a general YAML parser. Unknown/extra keys are
  passed through best-effort; an unparseable section degrades to empty (graceful)
  and never raises — the region hides when the file is absent or empty.
- **Read-only.** No files are written; the queue state machine is untouched.
- **AIPOS-288 FIX-2: Bilingual fields.** Optional ``_en`` variant fields enable
  English content without translating original declarations. EN mode prefers
  ``_en`` fields (e.g., ``description_en``); falls back to original when absent.
  ZH mode always uses original fields. Supported: ``portal.description_en``,
  ``collab_mode_en``, ``topology_en``, ``workers_en[]`` (parallel array),
  ``advisor_en``, ``advisor_note_en``; ``current_en``; ``milestones[].title_en``.
  Backward-compatible: maps without ``_en`` fields work unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.aipos_cli.adapter_response import make_response
from tools.aipos_cli.workspace_config import has_workspace_queue

READ_SAFETY_NOTICE = "Read-only local Board adapter call. No files are written."

PROJECT_MAP_REL = "governance/project-map.md"
DECISION_LOG_REL = "governance/decision_log"
DIRECTION_LOG_REL = "governance/direction_log"  # backward compat alias

# AIPOS-275 F-275-1: tolerate optional parenthetical date suffix (e.g. "## 2026-07-29(a) — …")
_HEADING_DATE_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})(?:\([^)]*\))?\s*[—–\-]\s*(.+?)\s*$")
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+\.\d+$")


# ---------------------------------------------------------------------------
# Schema-targeted frontmatter parser (PyYAML-free, zero-dep).
#
# Handles exactly the documented project-map.md shape:
#   - top-level scalars (map_version, updated, current, ...)
#   - portal: a nested map whose values are scalars OR a single nested list
#     (workers: [- ...])
#   - milestones: a list of maps, each {id, title, refs: [inline, list]}
#   - in_flight / next / horizon: lists of scalars
# Best-effort: an unparseable block degrades to None; unknown keys pass through.
# ---------------------------------------------------------------------------


def _strip_scalar(text: str) -> Any:
    """Strip surrounding quotes; coerce plain scalars (int/float/bool/null) to
    match standard YAML scalar semantics so ``map_version: 1`` is an int."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~", ""):
        return None if lowered != "" else text
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _parse_value(text: str) -> Any:
    """An inline value: a flow list ``[a, b]`` or a scalar."""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_strip_scalar(part) for part in inner.split(",")]
    return _strip_scalar(text)


def _tokenize(frontmatter: str) -> list[tuple[int, str]]:
    toks: list[tuple[int, str]] = []
    for raw in frontmatter.splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        toks.append((indent, raw.strip()))
    return toks


def _parse_list_of_scalars(block: list[tuple[int, str]]) -> list[str]:
    return [_strip_scalar(content[2:]) for _indent, content in block]


def _parse_list_of_maps(block: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """A list of maps: items start with ``- key: value``; sub-fields are deeper."""
    base = min(indent for indent, content in block if content.startswith("- "))
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for indent, content in block:
        if indent == base and content.startswith("- "):
            if current is not None:
                items.append(current)
            current = {}
            rest = content[2:].strip()
            if ":" in rest:
                key, _, val = rest.partition(":")
                current[key.strip()] = _parse_value(val) if val.strip() else None
            elif rest:
                current["_"] = _strip_scalar(rest)
        elif current is not None and ":" in content:
            key, _, val = content.partition(":")
            current[key.strip()] = _parse_value(val) if val.strip() else None
    if current is not None:
        items.append(current)
    return items


def _parse_nested_map(block: list[tuple[int, str]]) -> dict[str, Any]:
    """A nested map (portal): scalar leaves + at most one nested scalar list."""
    base = min(indent for indent, _ in block)
    result: dict[str, Any] = {}
    i = 0
    n = len(block)
    while i < n:
        indent, content = block[i]
        if indent == base and ":" in content and not content.startswith("- "):
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = _parse_value(val)
                i += 1
                continue
            # bare ``key:`` — gather the nested scalar list (workers:)
            items: list[str] = []
            j = i + 1
            while j < n and block[j][0] > base and block[j][1].startswith("- "):
                items.append(_strip_scalar(block[j][1][2:]))
                j += 1
            result[key] = items if items else None
            i = j
        else:
            i += 1
    return result


def _parse_block(block: list[tuple[int, str]]) -> Any:
    if not block:
        return None
    # Decide by the BASE-indent items: if they are ``- `` items this is a list
    # (scalars or maps); otherwise it is a nested map that may itself contain a
    # deeper ``- `` list under one of its keys (e.g. portal.workers).
    base = min(indent for indent, _ in block)
    base_items = [content for indent, content in block if indent == base]
    base_are_dashes = bool(base_items) and all(c.startswith("- ") for c in base_items)
    if base_are_dashes:
        has_subfields = any(indent > base and not content.startswith("- ") for indent, content in block)
        if has_subfields:
            return _parse_list_of_maps(block)
        return _parse_list_of_scalars(block)
    return _parse_nested_map(block)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text
    frontmatter = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")

    toks = _tokenize(frontmatter)
    result: dict[str, Any] = {}
    i = 0
    n = len(toks)
    while i < n:
        indent, content = toks[i]
        if indent != 0 or ":" not in content:
            i += 1
            continue
        key, _, val = content.partition(":")
        key = key.strip()
        val = val.strip()
        if val:
            result[key] = _parse_value(val)
            i += 1
            continue
        # bare ``key:`` — gather its indented block
        j = i + 1
        block: list[tuple[int, str]] = []
        while j < n and toks[j][0] > 0:
            block.append(toks[j])
            j += 1
        result[key] = _parse_block(block)
        i = j
    return result, body


# ---------------------------------------------------------------------------
# direction_log: latest N dated headings (for the current-node popup).
# ---------------------------------------------------------------------------


def _read_direction_log_recent(governance_dir: Path, limit: int = 3) -> list[dict[str, str]]:
    """Latest ``limit`` decision entries. Newest first.
    
    Supports both structures:
    - New: decision_log/<YYYY-MM>/<DD>-<seq>-<slug>.md (one entry per file)
    - Old: decision_log/<YYYY-MM>-direction-decisions.md (multiple ## headings)
    
    Backward compatibility: reads decision_log/ first; falls back to direction_log/ if absent.
    """
    # Prefer decision_log/, fall back to direction_log/
    dl_dir = governance_dir / "decision_log"
    if not dl_dir.is_dir():
        dl_dir = governance_dir / "direction_log"
    if not dl_dir.is_dir():
        return []
    
    entries: list[tuple[str, str]] = []  # (date, title)
    
    # Check for new structure: <YYYY-MM>/ subdirectories
    month_dirs = [p for p in dl_dir.iterdir() if p.is_dir() and re.match(r"\d{4}-\d{2}", p.name)]
    
    if month_dirs:
        # New structure: read individual entry files from month directories
        for month_dir in sorted(month_dirs, reverse=True):
            for entry_file in sorted(month_dir.glob("*.md"), reverse=True):
                # Skip INDEX.md
                if entry_file.name == "INDEX.md":
                    continue
                try:
                    text = entry_file.read_text(encoding="utf-8", errors="replace")
                    # Extract heading from first line
                    for line in text.splitlines():
                        match = _HEADING_DATE_RE.match(line)
                        if match:
                            entries.append((match.group(1), match.group(2).strip()))
                            break  # Only first heading per file
                except OSError:
                    continue
    else:
        # Old structure: scan monthly aggregate files for ## headings
        for path in sorted(dl_dir.glob("*.md"), reverse=True):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                match = _HEADING_DATE_RE.match(line)
                if match:
                    entries.append((match.group(1), match.group(2).strip()))
    
    # Sort by date descending
    entries.sort(key=lambda pair: pair[0], reverse=True)
    return [{"date": date, "title": title} for date, title in entries[:limit]]


# ---------------------------------------------------------------------------
# Public adapter
# ---------------------------------------------------------------------------


def get_project_map(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Read the project milestone map declaration. Read-only.

    Returns ``available=False`` (graceful hide) when the file is absent. Never
    raises — parse problems degrade to a WARN verdict with available data.
    """
    operation = "get_project_map"
    try:
        resolved = Path(repo_root).resolve() if repo_root is not None else None
        if resolved is None or not has_workspace_queue(resolved):
            return _empty(operation)
        map_path = resolved / PROJECT_MAP_REL
        if not map_path.is_file():
            return _empty(operation)
        text = map_path.read_text(encoding="utf-8", errors="replace")
        meta, _body = _parse_frontmatter(text)

        milestones_raw = meta.get("milestones")
        milestones: list[dict[str, Any]] = []
        if isinstance(milestones_raw, list):
            for item in milestones_raw:
                if isinstance(item, dict):
                    milestones.append({
                        "id": str(item.get("id") or "").strip() or None,
                        "title": str(item.get("title") or "").strip(),
                        "title_en": str(item.get("title_en") or "").strip(),
                        "refs": [
                            str(ref).strip() for ref in item.get("refs", [])
                            if isinstance(item.get("refs"), list) and str(ref).strip()
                        ] if isinstance(item.get("refs"), list) else [],
                    })

        portal_raw = meta.get("portal")
        portal = portal_raw if isinstance(portal_raw, dict) else {}

        # Initialize warnings list early
        warnings: list[str] = []
        
        # AIPOS-276: in_flight deprecated — read for backward compat but ignore; "进行中"
        # is derived from queue state (claimed/pending code tasks) in the frontend.
        in_flight_raw = meta.get("in_flight")
        if in_flight_raw is not None:
            warnings.append("project-map.md contains deprecated 'in_flight' field; this field is ignored. Remove it and rely on queue-derived '进行中' display.")
        
        nxt = _as_str_list(meta.get("next"))
        horizon = _as_str_list(meta.get("horizon"))
        current = _as_str(meta.get("current"))

        governance_dir = resolved / "governance"
        direction_recent = _read_direction_log_recent(governance_dir)

        verdict = "PASS"
        if not milestones and not current:
            warnings.append("project-map.md parsed but has no milestones/current; region hidden.")
            verdict = "WARN"
        elif warnings:  # AIPOS-276: deprecation warnings should result in WARN verdict
            verdict = "WARN"

        data = {
            "available": True,
            "map_version": meta.get("map_version"),
            "updated": _as_str(meta.get("updated")),
            "source_path": PROJECT_MAP_REL,
            "portal": {
                "description": _as_str(portal.get("description")),
                "description_en": _as_str(portal.get("description_en")),
                "collab_mode": _as_str(portal.get("collab_mode")),
                "collab_mode_en": _as_str(portal.get("collab_mode_en")),
                "topology": _as_str(portal.get("topology")),
                "topology_en": _as_str(portal.get("topology_en")),
                "workers": _as_str_list(portal.get("workers")),
                "workers_en": _as_str_list(portal.get("workers_en")),
                "advisor": _as_str(portal.get("advisor")),
                "advisor_en": _as_str(portal.get("advisor_en")),
                "advisor_note_en": _as_str(portal.get("advisor_note_en")),
            },
            "milestones": milestones,
            "current": current,
            "current_en": _as_str(meta.get("current_en")),
            "in_flight": [],  # AIPOS-276: always empty; frontend derives from queue
            "next": nxt,
            "horizon": horizon,
            "direction_log_recent": direction_recent,
            "writes_enabled": False,
        }
        return make_response(
            ok=True,
            verdict=verdict,
            operation=operation,
            dry_run=False,
            data=data,
            summary={
                "available": True,
                "milestones": len(milestones),
                "near_term": len(nxt),  # AIPOS-276: in_flight removed, only count next
                "horizon": len(horizon),
                "direction_log_recent": len(direction_recent),
            },
            warnings=warnings,
            blocking_reasons=[],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[],
        )
    except Exception as exc:  # never raise on a read surface
        return make_response(
            ok=False,
            verdict="BLOCK",
            operation=operation,
            dry_run=False,
            data={"available": False},
            summary={"available": False},
            warnings=[],
            blocking_reasons=[str(exc) or exc.__class__.__name__],
            needs_owner_reasons=[],
            owner_confirmation_required=False,
            owner_confirmation_reasons=[],
            safety_notice=READ_SAFETY_NOTICE,
            errors=[{"category": "INTERNAL_ERROR", "message": str(exc) or exc.__class__.__name__}],
        )


def _empty(operation: str) -> dict[str, Any]:
    return make_response(
        ok=True,
        verdict="PASS",
        operation=operation,
        dry_run=False,
        data={"available": False, "writes_enabled": False},
        summary={"available": False},
        warnings=[],
        blocking_reasons=[],
        needs_owner_reasons=[],
        owner_confirmation_required=False,
        owner_confirmation_reasons=[],
        safety_notice=READ_SAFETY_NOTICE,
        errors=[],
    )


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
