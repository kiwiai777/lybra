from __future__ import annotations

from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    return text


def _fallback_parse(frontmatter: str) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    current_list_key: str | None = None

    for line_no, raw_line in enumerate(frontmatter.splitlines(), start=1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_list_key is None:
                warnings.append(f"Line {line_no}: list item without a key")
                continue
            metadata.setdefault(current_list_key, [])
            value = _parse_scalar(stripped[2:])
            if not isinstance(metadata[current_list_key], list):
                warnings.append(
                    f"Line {line_no}: list item found after scalar for key {current_list_key}"
                )
                continue
            metadata[current_list_key].append(value)
            continue
        if ":" not in line:
            warnings.append(f"Line {line_no}: could not parse frontmatter line: {stripped}")
            current_list_key = None
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            warnings.append(f"Line {line_no}: empty key")
            current_list_key = None
            continue
        parsed_value = _parse_scalar(value)
        metadata[key] = parsed_value
        current_list_key = key if parsed_value is None else None
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
