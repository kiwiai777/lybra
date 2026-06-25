from __future__ import annotations

import re
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


# AIPOS-218: the stdlib fallback must parse exactly the FLAT-core subset Lybra's own writers emit
# (see draft_writer/_yaml_scalar, record_writer/_yaml_scalar) with the SAME result PyYAML would give —
# scalars, booleans, ints, floats, null/empty, single-/double-quoted scalars, and flat scalar lists.
# The accountability core (task cards + publish/claim/return/audit records) is FLAT, so this keeps the
# gate core truly zero-dependency. Nested YAML surfaces are PyYAML-only and fail loudly (not here).
_INT_RE = re.compile(r"^[-+]?[0-9]+$")
_FLOAT_RE = re.compile(r"^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$")


def _unquote_single(text: str) -> str:
    # writers single-quote with '' as the escaped single quote (record_writer/draft_writer _yaml_scalar)
    return text[1:-1].replace("''", "'")


def _unquote_double(text: str) -> str:
    # writers never emit double-quoted scalars; supported for forward-safety with minimal escapes.
    inner = text[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    # quoted scalars are strings verbatim — NO type coercion happens inside quotes (matches PyYAML).
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return _unquote_single(text)
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _unquote_double(text)
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "~"):
        return None
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _sub_scalar(value: str) -> Any:
    # a nested-map leaf value: empty → None, [] → empty list, else a scalar (AIPOS-218 WS1b).
    if value == "":
        return None
    if value == "[]":
        return []
    return _parse_scalar(value)


def _fallback_parse(frontmatter: str) -> tuple[dict[str, Any], list[str]]:
    """Parse exactly the subset Lybra's writers emit (AIPOS-218): top-level scalars / bools / ints /
    floats / null / quoted scalars / flat scalar lists / explicit ``[]`` / and **bounded nested maps
    of depth 1** (the workspace template manifests: ``output_policy:`` etc.). Block list items may be
    at indent 0 (record style) or indented (manifest style). Sequences-of-mappings and depth ≥ 2 are
    NOT parsed — they produce a warning and an absent value (never a silent mis-parse).
    """
    metadata: dict[str, Any] = {}
    warnings: list[str] = []

    toks: list[tuple[int, int, str]] = []
    for line_no, raw_line in enumerate(frontmatter.splitlines(), start=1):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        toks.append((line_no, indent, raw_line.strip()))

    n = len(toks)
    i = 0
    while i < n:
        line_no, indent, content = toks[i]
        if indent != 0:
            warnings.append(f"Line {line_no}: unexpected indentation")
            i += 1
            continue
        if content.startswith("- "):
            warnings.append(f"Line {line_no}: list item without a key")
            i += 1
            continue
        if ":" not in content:
            warnings.append(f"Line {line_no}: could not parse frontmatter line: {content}")
            i += 1
            continue
        key, _, value = content.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            warnings.append(f"Line {line_no}: empty key")
            i += 1
            continue
        if value == "[]":
            metadata[key] = []
            i += 1
            continue
        if value != "":
            metadata[key] = _parse_scalar(value)
            i += 1
            continue
        # bare ``key:`` — its block value is the run of following list-item / indented child lines.
        j = i + 1
        children: list[tuple[int, int, str]] = []
        while j < n and (toks[j][2].startswith("- ") or toks[j][1] > 0):
            children.append(toks[j])
            j += 1
        if not children:
            metadata[key] = None
        elif all(c[2].startswith("- ") for c in children):
            metadata[key] = [_parse_scalar(c[2][2:]) for c in children]
        elif all(c[1] > 0 and ":" in c[2] and not c[2].startswith("- ") for c in children) and len(
            {c[1] for c in children}
        ) == 1:
            submap: dict[str, Any] = {}
            for c in children:
                sub_key, _, sub_val = c[2].partition(":")
                sub_key = sub_key.strip()
                if not sub_key:
                    warnings.append(f"Line {c[0]}: empty key")
                    continue
                submap[sub_key] = _sub_scalar(sub_val.strip())
            metadata[key] = submap
        else:
            # sequences-of-mappings or depth >= 2 — out of the supported subset; do NOT silently
            # mis-parse (the manifest contract test guarantees bundled files never reach here).
            warnings.append(f"Line {line_no}: unsupported nested structure for key {key}")
            metadata[key] = None
        i = j
    return metadata, warnings


def parse_markdown_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    warnings: list[str] = []
    if not text.startswith("---"):
        return {}, text, warnings

    lines = text.splitlines()
    end_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        return {}, text, ["Frontmatter start found without closing delimiter"]

    frontmatter = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")

    if yaml is not None:
        try:
            data = yaml.safe_load(frontmatter) or {}
            if not isinstance(data, dict):
                return {}, body, ["Frontmatter did not parse to a mapping"]
            return data, body, warnings
        except Exception as exc:
            warnings.append(f"PyYAML parse failed: {exc}")

    data, fallback_warnings = _fallback_parse(frontmatter)
    warnings.extend(fallback_warnings)
    return data, body, warnings
