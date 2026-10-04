from __future__ import annotations

import re
from typing import Any




try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None


# AIPOS-218 → AIPOS-F98: the stdlib fallback (used when PyYAML is absent — the product's runtime is zero-dependency)
# parses the block-YAML subset Lybra's writers actually emit (record_writer.render_markdown via yaml.safe_dump
# default_flow_style=False, or the stdlib emitter) and returns EXACTLY what yaml.safe_load returns for it:
#   - block mappings at any depth (e.g. card ``lane: {repo, paths: [..], roles: [..]}``, template ``output_policy``);
#   - block sequences, both indentless (safe_dump style, ``- x`` at the parent key's indent) and indented;
#     sequence items may be scalars or compact mappings (``- round: 1`` + sibling keys, card ``rework_rounds``);
#   - empty flow collections ``[]`` / ``{}``;
#   - plain scalars resolved with PyYAML's YAML 1.1 implicit resolvers (null / bool / int / float / timestamp);
#   - single-quoted and double-quoted scalars, including multi-line (line folding) and the full escape set;
#   - full-line and trailing comments.
# Anything outside the subset (anchors / aliases / tags / block scalars ``|`` ``>`` / non-empty flow collections /
# multi-line plain scalars / complex keys / tab indentation / structural errors) is NOT guessed: _fallback_parse
# raises FrontmatterUnsupportedError (key path + line), and parse_markdown_frontmatter turns that into an empty
# mapping plus a warning — the module's existing error convention. A key is never silently dropped to None.
_BOOL_RE = re.compile(r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$")
_BOOL_TRUE = frozenset(("yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON"))
_FLOAT_RE = re.compile(
    r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
        |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
        |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
        |[-+]?\.(?:inf|Inf|INF)
        |\.(?:nan|NaN|NAN))$""",
    re.X,
)
_INT_RE = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+
        |[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$""",
    re.X,
)
_NULL_RE = re.compile(r"^(?:~|null|Null|NULL|)$")
_TIMESTAMP_RE = re.compile(
    r"""^(?P<year>[0-9][0-9][0-9][0-9])
        -(?P<month>[0-9][0-9]?)
        -(?P<day>[0-9][0-9]?)
        (?:(?:[Tt]|[ \t]+)
        (?P<hour>[0-9][0-9]?)
        :(?P<minute>[0-9][0-9])
        :(?P<second>[0-9][0-9])
        (?:\.(?P<fraction>[0-9]*))?
        (?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)
        (?::(?P<tz_minute>[0-9][0-9]))?))?)?$""",
    re.X,
)
# the resolver's timestamp pattern (stricter than the constructor's: date-only form needs 2-digit month/day)
_TIMESTAMP_RESOLVE_RE = re.compile(
    r"""^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]
        |[0-9][0-9][0-9][0-9] -[0-9][0-9]? -[0-9][0-9]?
         (?:[Tt]|[ \t]+)[0-9][0-9]?
         :[0-9][0-9] :[0-9][0-9] (?:\.[0-9]*)?
         (?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$""",
    re.X,
)
_DQ_ESCAPES = {
    "0": "\0", "a": "\x07", "b": "\x08", "t": "\x09", "\t": "\x09", "n": "\x0a", "v": "\x0b", "f": "\x0c",
    "r": "\x0d", "e": "\x1b", " ": " ", '"': '"', "\\": "\\", "/": "/", "N": "\x85", "_": "\xa0",
    "L": " ", "P": " ",
}
_DQ_ESCAPE_CODES = {"x": 2, "u": 4, "U": 8}
# characters that cannot start a plain scalar in block context (``-`` ``?`` ``:`` only when followed by a space)
_PLAIN_FORBIDDEN_LEADS = frozenset("[]{},#&*!|>'\"%@`")
_LINE_BREAK_RE = re.compile("(\r\n|[\r\n\x85\u2028\u2029])")
# PyYAML Reader.NON_PRINTABLE: such characters make yaml.safe_load fail, so the fallback refuses them too
_NON_PRINTABLE_RE = re.compile("[^\x09\x0A\x0D\x20-\x7E\x85\xA0-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]")
_INF = float("inf")


class FrontmatterUnsupportedError(ValueError):
    """AIPOS-F98: the zero-dependency frontmatter parser met a structure it does not parse. Carries the
    frontmatter-relative 1-based line number and the dotted key path.

    ``yaml_invalid`` = the construct is a YAML error as well (yaml.safe_load fails on it too). Only such errors may
    be salvaged at top-level-entry granularity (see parse_markdown_frontmatter); a construct that is valid YAML but
    outside the subset is never salvaged — a partial mapping would silently diverge from what the YAML says."""

    def __init__(self, line_no: int, key_path: str, reason: str, *, yaml_invalid: bool) -> None:
        self.line_no = line_no
        self.key_path = key_path
        self.reason = reason
        self.yaml_invalid = yaml_invalid
        super().__init__(f"Line {line_no}: key {key_path or '<root>'}: {reason}")


def _construct_int(text: str) -> int:
    # PyYAML SafeConstructor.construct_yaml_int, verbatim semantics
    value = text.replace("_", "")
    sign = -1 if value[0] == "-" else 1
    if value[0] in "+-":
        value = value[1:]
    if value == "0":
        return 0
    if value.startswith("0b"):
        return sign * int(value[2:], 2)
    if value.startswith("0x"):
        return sign * int(value[2:], 16)
    if value[0] == "0":
        return sign * int(value, 8)
    if ":" in value:
        total = 0
        base = 1
        for part in reversed(value.split(":")):
            total += int(part) * base
            base *= 60
        return sign * total
    return sign * int(value)


def _construct_float(text: str) -> float:
    # PyYAML SafeConstructor.construct_yaml_float, verbatim semantics
    value = text.replace("_", "").lower()
    sign = -1 if value[0] == "-" else 1
    if value[0] in "+-":
        value = value[1:]
    if value == ".inf":
        return sign * _INF
    if value == ".nan":
        return float("nan")
    if ":" in value:
        total = 0.0
        base = 1
        for part in reversed(value.split(":")):
            total += float(part) * base
            base *= 60
        return sign * total
    return sign * float(value)


def _construct_timestamp(text: str) -> Any:
    # PyYAML SafeConstructor.construct_yaml_timestamp, verbatim semantics
    import datetime as _dt

    values = _TIMESTAMP_RE.match(text).groupdict()  # type: ignore[union-attr]
    year, month, day = int(values["year"]), int(values["month"]), int(values["day"])
    if not values["hour"]:
        return _dt.date(year, month, day)
    hour, minute, second = int(values["hour"]), int(values["minute"]), int(values["second"])
    fraction = 0
    tzinfo = None
    if values["fraction"]:
        frac = values["fraction"][:6]
        while len(frac) < 6:
            frac += "0"
        fraction = int(frac)
    if values["tz_sign"]:
        delta = _dt.timedelta(hours=int(values["tz_hour"]), minutes=int(values["tz_minute"] or 0))
        if values["tz_sign"] == "-":
            delta = -delta
        tzinfo = _dt.timezone(delta)
    elif values["tz"]:
        tzinfo = _dt.timezone.utc
    return _dt.datetime(year, month, day, hour, minute, second, fraction, tzinfo=tzinfo)


def _resolve_plain(text: str, line_no: int, key_path: str) -> Any:
    """A plain scalar → the value yaml.safe_load gives it (PyYAML implicit resolvers, in registration order)."""
    if _BOOL_RE.match(text):
        return text in _BOOL_TRUE
    if _FLOAT_RE.match(text):
        return _construct_float(text)
    if _INT_RE.match(text):
        return _construct_int(text)
    if text == "<<" or text == "=":
        raise FrontmatterUnsupportedError(line_no, key_path, f"plain scalar {text!r} is a YAML merge/value indicator", yaml_invalid=True)
    if _NULL_RE.match(text):
        return None
    if _TIMESTAMP_RESOLVE_RE.match(text):
        try:
            return _construct_timestamp(text)
        except ValueError as exc:  # e.g. month 13 — PyYAML raises too
            raise FrontmatterUnsupportedError(line_no, key_path, f"invalid timestamp {text!r}: {exc}", yaml_invalid=True) from exc
    return text


def _is_seq_item(text: str) -> bool:
    return text == "-" or text.startswith("- ") or text.startswith("-\t")


class _BlockParser:
    """Recursive-descent parser over the frontmatter's raw lines (see the module comment for the subset)."""

    def __init__(self, frontmatter: str, salvage: bool = False) -> None:
        # salvage: a YAML error inside one top-level entry drops that entry (recorded in self.salvaged) instead of
        # failing the whole document; anything that is not a YAML error still fails the whole document.
        self.salvage = salvage
        self.salvaged: list[FrontmatterUnsupportedError] = []
        self.top_spans: list[tuple[Any, int, int]] = []  # (key, first line idx, line idx after the value)
        bad = _NON_PRINTABLE_RE.search(frontmatter)
        if bad is not None:
            line_no = len(_LINE_BREAK_RE.findall(frontmatter[: bad.start()])) + 1
            raise FrontmatterUnsupportedError(line_no, "", f"non-printable character {bad.group()!r} (YAML rejects it)", yaml_invalid=True)
        # YAML 1.1 line breaks; each line keeps the break that ends it (PyYAML: \r\n \r \n \x85 → '\n',
        # \u2028 / \u2029 kept as themselves — they matter when a quoted scalar folds across lines)
        parts = _LINE_BREAK_RE.split(frontmatter)
        self.lines = parts[0::2]
        self.breaks = [("\n" if b in ("\r\n", "\r", "\n", "\x85") else b) for b in parts[1::2]] + ["\n"]
        # compact ``- key: v`` items re-enter a line at a deeper column: idx -> (indent, text)
        self.virtual: dict[int, tuple[int, str]] = {}
        # tabs seen INSIDE quoted scalars, per line (values accumulate; keys are re-scanned, so assigned).
        # PyYAML rejects a tab anywhere else as a token separator / indentation; parse_document checks the rest.
        self.quoted_tabs: dict[int, int] = {}
        self.quoted_key_tabs: dict[int, int] = {}

    # ---- line access -------------------------------------------------------------------------------------------
    def _line(self, idx: int) -> tuple[int, str]:
        if idx in self.virtual:
            return self.virtual[idx]
        raw = self.lines[idx]
        stripped = raw.lstrip(" ")
        if stripped.startswith("\t") and stripped.strip(" \t"):
            raise FrontmatterUnsupportedError(idx + 1, "", "tab character in indentation", yaml_invalid=True)
        return len(raw) - len(stripped), stripped.rstrip(" \t")

    def _next_content(self, idx: int) -> int | None:
        """Index of the next line that is neither blank nor a full-line comment."""
        while idx < len(self.lines):
            if idx in self.virtual:
                return idx
            stripped = self.lines[idx].strip(" \t")
            if stripped and not stripped.startswith("#"):
                return idx
            idx += 1
        return None

    # ---- documents and block nodes -----------------------------------------------------------------------------
    def parse_document(self) -> dict[str, Any]:
        value = self._parse_top()
        for idx, raw in enumerate(self.lines):
            if raw.count("\t") > self.quoted_tabs.get(idx, 0) + self.quoted_key_tabs.get(idx, 0):
                owner = next((key for key, start, end in self.top_spans if start <= idx < end), None)
                # a tab inside a comment is valid YAML this parser does not read; when a '#' precedes the line's last
                # tab the tab may sit in a comment, so it is not treated as a YAML error (never salvaged)
                exc = FrontmatterUnsupportedError(
                    idx + 1, "" if owner is None else str(owner),
                    "tab character outside a quoted scalar (incl. comments) is not supported",
                    yaml_invalid="#" not in raw[: raw.rfind("\t")],
                )
                if not (self.salvage and exc.yaml_invalid):
                    raise exc
                self.salvaged.append(exc)
                if owner is not None:
                    value.pop(owner, None)
        return value

    def _parse_top(self) -> dict[str, Any]:
        first = self._next_content(0)
        if first is None:
            return {}
        indent, text = self._line(first)
        if self._mapping_entry(text, first, "") is None:
            # valid YAML that is not a mapping (sequence / scalar document) or a construct this parser does not read
            raise FrontmatterUnsupportedError(first + 1, "", "frontmatter is not a block mapping", yaml_invalid=False)
        value, nxt = self._parse_mapping(first, indent, "", top=True)
        rest = self._next_content(nxt)
        if rest is not None:
            raise FrontmatterUnsupportedError(rest + 1, "", "unexpected content after the top-level mapping", yaml_invalid=True)
        return value

    def _parse_block_node(self, idx: int, parent_indent: int, key_path: str, allow_indentless: bool) -> tuple[Any, int]:
        """The block node that is the value of an empty ``key:`` / ``-`` on the line before ``idx``."""
        j = self._next_content(idx)
        if j is None:
            return None, idx
        indent, text = self._line(j)
        if allow_indentless and indent == parent_indent and _is_seq_item(text):
            return self._parse_sequence(j, indent, key_path)
        if indent <= parent_indent:
            return None, idx
        if _is_seq_item(text):
            return self._parse_sequence(j, indent, key_path)
        if self._mapping_entry(text, j, key_path) is not None:
            return self._parse_mapping(j, indent, key_path)
        if text[:1] in ("'", '"'):
            value, nxt = self._parse_quoted_node(j, text, key_path)
            self._expect_dedent(nxt, parent_indent, key_path)
            return value, nxt
        if text in ("[]", "{}") or text.startswith(("[] #", "{} #")):
            self._expect_dedent(j + 1, parent_indent, key_path)
            return ([] if text.startswith("[") else {}), j + 1
        raise FrontmatterUnsupportedError(j + 1, key_path, "scalar on its own line below the key (multi-line plain scalar)", yaml_invalid=False)

    def _parse_mapping(self, idx: int, indent: int, parent_path: str, top: bool = False) -> tuple[dict[str, Any], int]:
        result: dict[Any, Any] = {}
        j: int | None = idx
        while j is not None:
            start = j
            path = ""
            try:
                line_indent, text = self._line(j)
                if line_indent < indent:
                    break
                if line_indent > indent:
                    # at the top level a stray deeper line most likely belongs to the entry before it: that entry is
                    # dropped with it (salvage never keeps a value the stray line may have been part of)
                    owner = self.top_spans[-1][0] if (top and self.top_spans) else None
                    if owner is not None:
                        result.pop(owner, None)
                    raise FrontmatterUnsupportedError(
                        j + 1, parent_path if owner is None else str(owner), "unexpected indentation", yaml_invalid=True
                    )
                entry = self._mapping_entry(text, j, parent_path)
                if entry is None:
                    # a line that is not ``key: value`` where a key is due is a YAML error — except the openers of
                    # valid YAML this parser does not read (complex key ``?``, anchored / tagged key, flow / block key)
                    raise FrontmatterUnsupportedError(
                        j + 1, parent_path, f"expected a 'key: value' line, got {text[:60]!r}",
                        yaml_invalid=text[:1] not in "?&!|>[{",
                    )
                key, rest = entry
                path = f"{parent_path}.{key}" if parent_path else str(key)
                value, nxt = self._parse_value(rest, j, indent, path, in_mapping=True)
            except FrontmatterUnsupportedError as exc:
                if not exc.key_path and path:
                    # a line-level error (e.g. tab indentation) inside this entry's value: name the entry
                    exc = FrontmatterUnsupportedError(exc.line_no, path, exc.reason, yaml_invalid=exc.yaml_invalid)
                if not (top and self.salvage and exc.yaml_invalid):
                    raise exc
                # salvage: drop this top-level entry (absent, never None) and resume at the next top-level key
                self.salvaged.append(exc)
                j = self._resync(start + 1, indent)
                continue
            result[key] = value
            if top:
                self.top_spans.append((key, start, nxt))
            j = self._next_content(nxt)
        return result, (j if j is not None else len(self.lines))

    def _resync(self, idx: int, indent: int) -> int | None:
        """Salvage: the next raw line at the top-level indent that reads as a ``key:`` entry (or a shallower line)."""
        for k in range(idx, len(self.lines)):
            raw = self.lines[k]
            stripped = raw.lstrip(" ")
            if not stripped.strip(" \t") or stripped.startswith(("#", "\t")):
                continue
            k_indent = len(raw) - len(stripped)
            if k_indent > indent:
                continue
            self.virtual.pop(k, None)
            if k_indent < indent:
                return k
            try:
                entry = self._mapping_entry(stripped.rstrip(" \t"), k, "")
            except FrontmatterUnsupportedError:
                continue
            if entry is not None:
                return k
        return None

    def _parse_sequence(self, idx: int, indent: int, key_path: str) -> tuple[list[Any], int]:
        result: list[Any] = []
        j: int | None = idx
        n = 0
        while j is not None:
            line_indent, text = self._line(j)
            if line_indent < indent or (line_indent == indent and not _is_seq_item(text)):
                break
            if line_indent > indent:
                raise FrontmatterUnsupportedError(j + 1, key_path, "unexpected indentation inside a sequence", yaml_invalid=True)
            item_path = f"{key_path}[{n}]"
            rest = text[1:]
            stripped = rest.lstrip(" \t")
            item_col = line_indent + 1 + (len(rest) - len(stripped))
            if stripped == "" or stripped.startswith("#"):
                value, nxt = self._parse_block_node(j + 1, indent, item_path, allow_indentless=False)
            elif _is_seq_item(stripped) or self._mapping_entry(stripped, j, item_path) is not None:
                # compact nested node: re-read this line at the item's column
                self.virtual[j] = (item_col, stripped)
                if _is_seq_item(stripped):
                    value, nxt = self._parse_sequence(j, item_col, item_path)
                else:
                    value, nxt = self._parse_mapping(j, item_col, item_path)
            else:
                value, nxt = self._parse_value(stripped, j, indent, item_path, in_mapping=False)
            result.append(value)
            n += 1
            j = self._next_content(nxt)
        return result, (j if j is not None else len(self.lines))

    # ---- entries and values ------------------------------------------------------------------------------------
    def _mapping_entry(self, text: str, idx: int, key_path: str) -> tuple[Any, str] | None:
        """``key: rest`` → (resolved key, rest text) or None when the line is not a mapping entry."""
        if not text or _is_seq_item(text):
            return None
        if text[0] in ("'", '"'):
            body, end = _scan_quoted_line(text, 0)
            if body is None:
                return None
            self.quoted_key_tabs[idx] = text[:end].count("\t")
            after = text[end:]
            stripped_after = after.lstrip(" ")
            if not (stripped_after == ":" or stripped_after.startswith(": ") or stripped_after.startswith(":\t")):
                return None
            colon = end + (len(after) - len(stripped_after))
            return body, text[colon + 1 :].strip(" \t")
        if text[0] in _PLAIN_FORBIDDEN_LEADS or text.startswith(("? ", ": ")) or text in ("?", ":"):
            return None
        pos = 0
        while True:
            colon = text.find(":", pos)
            if colon < 0:
                return None
            hash_pos = text.find(" #")
            if 0 <= hash_pos < colon:
                return None
            if colon == len(text) - 1 or text[colon + 1] in " \t":
                break
            pos = colon + 1
        raw_key = text[:colon].rstrip(" \t")
        if not raw_key:
            return None
        if raw_key == "<<":
            raise FrontmatterUnsupportedError(idx + 1, key_path, "YAML merge key '<<' is not supported", yaml_invalid=False)
        key = _resolve_plain(raw_key, idx + 1, key_path)
        return key, text[colon + 1 :].strip(" \t")

    def _parse_value(self, rest: str, idx: int, owner_indent: int, key_path: str, in_mapping: bool) -> tuple[Any, int]:
        """Value after ``key:`` (in_mapping) or after ``- `` — inline scalar, empty flow collection, or a block node."""
        if rest == "" or rest.startswith("#"):
            return self._parse_block_node(idx + 1, owner_indent, key_path, allow_indentless=in_mapping)
        lead = rest[0]
        if lead in ("'", '"'):
            value, nxt = self._parse_quoted_node(idx, rest, key_path)
            self._expect_dedent(nxt, owner_indent, key_path)
            return value, nxt
        if lead in ("[", "{"):
            body = rest.split(" #", 1)[0].rstrip(" \t")
            inner = body[1:-1].strip(" \t") if len(body) >= 2 else None
            if inner == "" and ((lead == "[" and body.endswith("]")) or (lead == "{" and body.endswith("}"))):
                self._expect_dedent(idx + 1, owner_indent, key_path)
                return ([] if lead == "[" else {}), idx + 1
            raise FrontmatterUnsupportedError(idx + 1, key_path, f"non-empty flow collection {body[:60]!r} is not supported", yaml_invalid=False)
        if lead in "|>":
            raise FrontmatterUnsupportedError(idx + 1, key_path, "block scalar ('|' / '>') is not supported", yaml_invalid=False)
        if lead in "&!":
            raise FrontmatterUnsupportedError(idx + 1, key_path, "anchors and tags are not supported", yaml_invalid=False)
        if lead == "*":
            # anchors are refused before any alias is reached, so an alias here never has its anchor: a YAML error
            raise FrontmatterUnsupportedError(idx + 1, key_path, "alias without an anchor ('*' cannot start a plain scalar)", yaml_invalid=True)
        if lead in _PLAIN_FORBIDDEN_LEADS or rest.startswith(("- ", "? ", ": ")) or rest in ("-", "?", ":"):
            raise FrontmatterUnsupportedError(
                idx + 1, key_path, f"value cannot start with {lead!r} as a plain scalar", yaml_invalid=lead != "?"
            )
        hash_pos = rest.find(" #")
        if hash_pos < 0:
            hash_pos = rest.find("\t#")
        plain = (rest[:hash_pos] if hash_pos >= 0 else rest).rstrip(" \t")
        if ": " in plain or ":\t" in plain or plain.endswith(":"):
            raise FrontmatterUnsupportedError(idx + 1, key_path, f"mapping value inside a plain scalar {plain[:60]!r}", yaml_invalid=True)
        nxt = self._next_content(idx + 1)
        if nxt is not None and self._line(nxt)[0] > owner_indent:
            raise FrontmatterUnsupportedError(nxt + 1, key_path, "multi-line plain scalar (continuation line) is not supported", yaml_invalid=False)
        return _resolve_plain(plain, idx + 1, key_path), idx + 1

    def _expect_dedent(self, idx: int, owner_indent: int, key_path: str) -> None:
        nxt = self._next_content(idx)
        if nxt is not None and self._line(nxt)[0] > owner_indent:
            raise FrontmatterUnsupportedError(nxt + 1, key_path, "unexpected indentation after the value", yaml_invalid=True)

    def _parse_quoted_node(self, idx: int, first: str, key_path: str) -> tuple[str, int]:
        """A single- or double-quoted scalar whose text on line ``idx`` is ``first`` (starting at the opening quote);
        may continue on following raw lines (YAML line folding)."""
        double = first[0] == '"'
        chunks: list[str] = []
        li = idx
        raw = self.lines[idx]
        # ``first`` is the rstripped suffix of the raw line; re-attach trailing whitespace (inside the quotes when the
        # scalar continues on the next line)
        line = raw[len(raw.rstrip(" \t")) - len(first) :]
        pos = 1
        while True:
            while pos < len(line):
                ch = line[pos]
                if not double and ch == "'":
                    if pos + 1 < len(line) and line[pos + 1] == "'":
                        chunks.append("'")
                        pos += 2
                        continue
                    return "".join(chunks), self._after_scalar(li, line, pos + 1, key_path)
                if double and ch == '"':
                    return "".join(chunks), self._after_scalar(li, line, pos + 1, key_path)
                if double and ch == "\\":
                    if pos + 1 == len(line):
                        # escaped line break: join without a space, keep the breaks of following empty lines
                        li, line, pos, breaks = self._fold(li, key_path)
                        chunks.extend(breaks)
                        continue
                    nxt = line[pos + 1]
                    if nxt in _DQ_ESCAPES:
                        self._count_quoted_tabs(li, nxt)
                        chunks.append(_DQ_ESCAPES[nxt])
                        pos += 2
                        continue
                    if nxt in _DQ_ESCAPE_CODES:
                        length = _DQ_ESCAPE_CODES[nxt]
                        digits = line[pos + 2 : pos + 2 + length]
                        if len(digits) != length or any(c not in "0123456789abcdefABCDEF" for c in digits):
                            raise FrontmatterUnsupportedError(li + 1, key_path, f"bad escape \\{nxt}{digits!r} in double-quoted scalar", yaml_invalid=True)
                        chunks.append(chr(int(digits, 16)))
                        pos += 2 + length
                        continue
                    raise FrontmatterUnsupportedError(li + 1, key_path, f"unknown escape \\{nxt} in double-quoted scalar", yaml_invalid=True)
                if ch in " \t":
                    end = pos
                    while end < len(line) and line[end] in " \t":
                        end += 1
                    self._count_quoted_tabs(li, line[pos:end])
                    if end == len(line):
                        pos = end  # trailing whitespace before a line break is folded away
                        break
                    chunks.append(line[pos:end])
                    pos = end
                    continue
                chunks.append(ch)
                pos += 1
            # line break inside the quotes → fold (PyYAML scan_flow_scalar_spaces): a lone '\n' break becomes
            # a space, a run of empty lines becomes their breaks, a \u2028/\u2029 break is kept as itself
            first_break = self.breaks[li]
            li, line, pos, breaks = self._fold(li, key_path)
            if first_break != "\n":
                chunks.append(first_break)
            elif not breaks:
                chunks.append(" ")
            chunks.extend(breaks)

    def _fold(self, li: int, key_path: str) -> tuple[int, str, int, list[str]]:
        breaks: list[str] = []
        li += 1
        while True:
            if li >= len(self.lines):
                raise FrontmatterUnsupportedError(li, key_path, "unterminated quoted scalar", yaml_invalid=True)
            line = self.lines[li]
            if line.startswith(("---", "...")) and (len(line) == 3 or line[3] in " \t"):
                raise FrontmatterUnsupportedError(li + 1, key_path, "document separator inside a quoted scalar", yaml_invalid=True)
            stripped = line.lstrip(" \t")
            self._count_quoted_tabs(li, line[: len(line) - len(stripped)])
            if stripped == "" and li + 1 < len(self.lines):
                breaks.append(self.breaks[li])
                li += 1
                continue
            return li, line, len(line) - len(stripped), breaks

    def _count_quoted_tabs(self, li: int, text: str) -> None:
        if "\t" in text:
            self.quoted_tabs[li] = self.quoted_tabs.get(li, 0) + text.count("\t")

    def _after_scalar(self, li: int, line: str, pos: int, key_path: str) -> int:
        tail = line[pos:]
        stripped = tail.strip(" \t")
        # PyYAML takes a '#' right after a closing quote as a comment too (no separating space needed)
        if stripped and not stripped.startswith("#"):
            raise FrontmatterUnsupportedError(li + 1, key_path, f"unexpected text after a quoted scalar: {stripped[:40]!r}", yaml_invalid=True)
        return li + 1


def _scan_quoted_line(text: str, start: int) -> tuple[str | None, int]:
    """A quoted scalar entirely on one line starting at ``text[start]`` → (value, index after closing quote);
    (None, -1) when it does not close on this line. Used for quoted mapping keys."""
    quote = text[start]
    out: list[str] = []
    pos = start + 1
    while pos < len(text):
        ch = text[pos]
        if quote == "'" and ch == "'":
            if pos + 1 < len(text) and text[pos + 1] == "'":
                out.append("'")
                pos += 2
                continue
            return "".join(out), pos + 1
        if quote == '"' and ch == '"':
            return "".join(out), pos + 1
        if quote == '"' and ch == "\\" and pos + 1 < len(text):
            nxt = text[pos + 1]
            if nxt in _DQ_ESCAPES:
                out.append(_DQ_ESCAPES[nxt])
                pos += 2
                continue
            if nxt in _DQ_ESCAPE_CODES:
                length = _DQ_ESCAPE_CODES[nxt]
                digits = text[pos + 2 : pos + 2 + length]
                if len(digits) == length and all(c in "0123456789abcdefABCDEF" for c in digits):
                    out.append(chr(int(digits, 16)))
                    pos += 2 + length
                    continue
            return None, -1
        out.append(ch)
        pos += 1
    return None, -1


def _fallback_parse(frontmatter: str, salvage: bool = False) -> tuple[dict[str, Any], list[str]]:
    """Zero-dependency frontmatter parse (AIPOS-218 / AIPOS-F98): the block-YAML subset in the module comment,
    with the same result yaml.safe_load gives.

    Strict (default): anything it does not parse raises FrontmatterUnsupportedError — never a partial mapping,
    never a key silently set to None; the warnings list is always empty on success.
    salvage=True (used only by parse_markdown_frontmatter): a YAML error confined to one top-level entry drops
    that entry (absent, not None) and is reported in the returned warnings with key path and file line; valid
    YAML outside the subset still raises (the whole document is refused).
    """
    parser = _BlockParser(frontmatter, salvage=salvage)
    data = parser.parse_document()
    return data, [
        f"Frontmatter key dropped (YAML error; zero-dependency parser): file line {exc.line_no + 1}, "
        f"key {exc.key_path or '<root>'}: {exc.reason}"
        for exc in parser.salvaged
    ]


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

    # Zero-dependency path (PyYAML absent), or salvage of a document PyYAML rejected. Either way the result is the
    # same: entries hit by a YAML error are dropped and named in the warnings (callers that read warnings — lint
    # FRONTMATTER_INVALID, my-tasks next_card — still see and attribute the bad card).
    try:
        data, fallback_warnings = _fallback_parse(frontmatter, salvage=True)
    except FrontmatterUnsupportedError as exc:
        # AIPOS-F98 fail-closed: valid YAML outside the parser's subset (or an unlocalisable error) → NO mapping at
        # all; never a key silently parsed to None. The warning names the key path and the file line.
        warnings.append(
            f"Frontmatter unsupported by the zero-dependency parser (fail-closed, no fields returned): "
            f"file line {exc.line_no + 1}, key {exc.key_path or '<root>'}: {exc.reason}"
        )
        return {}, body, warnings
    warnings.extend(fallback_warnings)
    return data, body, warnings
# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
