from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tools.aipos_cli.clock import file_slug
from tools.aipos_cli.frontmatter import _resolve_plain, parse_markdown_frontmatter
from tools.aipos_cli.records import expected_claim_log_path, expected_closure_record_path, expected_return_record_path, expected_session_record_path
from tools.schema_loader import get_enum_values
from tools.schema_constants import RecordType, Verdict

# AIPOS-F22B: YAML 序列化器 (可选依赖, zerodep 核心使用 stdlib fallback)
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None  # noqa: F841

# Record type constants from enums.schema.json (single source)
# FND-47: 禁字面量漂移 - 所有 record_type 值从 enums.schema 读取
_RECORD_TYPE_ENUM: list[str] | None = None

def _get_record_type(name: str) -> str:
    """Get record_type value from enums.schema.json.
    
    Args:
        name: enum value name (e.g., 'audit_verdict', 'claim', 'return')
    
    Returns:
        The value from enums.schema (single source)
    
    Raises:
        ValueError: If the record type is not defined in enums.schema
    """
    global _RECORD_TYPE_ENUM
    if _RECORD_TYPE_ENUM is None:
        _RECORD_TYPE_ENUM = get_enum_values("record_type")
    if name not in _RECORD_TYPE_ENUM:
        raise ValueError(
            f"record_type '{name}' not found in enums.schema.json. "
            f"Available: {_RECORD_TYPE_ENUM}"
        )
    return name




# ---------------------------------------------------------------------------
# AIPOS-F109 件①(H10): 记录落点唯一读取口——声明 = transitions.schema record_locations(节点记录经 node_ref 指节点
# record.location, 非节点记录模板在本表)。产品代码零写死 records/<子目录>: 落点一律经 record_dir / record_root。
# 原 RECORDS_ROOT/CLAIMS_ROOT/… 常量与 config.schema 两处零读取方的记录路径声明退役。
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_]+\}")


def _product_transitions() -> dict[str, Any]:
    """产品 transitions.schema(代码仓 schema/, 经 schema_loader 缓存)。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    return load_schema("transitions", code_repo_schema_root())


def _record_kinds() -> dict[str, dict[str, Any]]:
    kinds = (_product_transitions().get("record_locations") or {}).get("kinds")
    if not isinstance(kinds, dict) or not kinds:
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError("transitions.schema.json record_locations.kinds 未声明(记录落点唯一声明, AIPOS-F109 件①)")
    return kinds


def record_location(kind: str, transitions_schema: dict[str, Any] | None = None) -> str:
    """记录类 kind 的落点模板(相对治理根)。节点记录读 transitions_schema(缺省 = 产品 schema)的节点 record.location
    (node_ref / record_key), 非节点记录读 record_locations.kinds[kind].location。未声明 = ValueError(fail-closed)。"""
    entry = _record_kinds().get(kind)
    if not isinstance(entry, dict):
        raise ValueError(f"记录类 {kind!r} 未在 transitions.schema record_locations.kinds 声明(已声明: {sorted(_record_kinds())})")
    if entry.get("node_ref"):
        schema = transitions_schema if transitions_schema is not None else _product_transitions()
        node = (schema.get("nodes") or {}).get(str(entry["node_ref"])) or {}
        decl = node.get(str(entry.get("record_key") or "record")) or {}
        location = decl.get("location") if isinstance(decl, dict) else None
    else:
        location = entry.get("location")
    if not isinstance(location, str) or not location.strip():
        raise ValueError(f"记录类 {kind!r} 的 location 未声明(transitions.schema record_locations.kinds.{kind})")
    return location


def record_kind_for_type(record_type: str) -> str:
    """写入器记录类型名(归一化: 小写、去 _log/_record 尾)→ 记录类(record_locations.kinds[*].record_types)。未声明 = ValueError。"""
    normalized = str(record_type).lower().replace("_log", "").replace("_record", "")
    for kind, entry in _record_kinds().items():
        if normalized in (entry.get("record_types") or []):
            return kind
    raise ValueError(f"Unknown record_type for schema lookup: {record_type} (normalized: {normalized}; 声明 transitions.schema record_locations.kinds[*].record_types)")


def _split_location(location: str) -> tuple[list[str], str]:
    parts = location.split("/")
    return parts[:-1], parts[-1]


def record_root(kind: str, transitions_schema: dict[str, Any] | None = None) -> Path:
    """记录类的根目录(相对治理根): 落点目录部分中第一个占位段之前的前缀, 如 5_tasks/records/claims。"""
    dir_parts, _filename = _split_location(record_location(kind, transitions_schema))
    static: list[str] = []
    for part in dir_parts:
        if _PLACEHOLDER_RE.search(part):
            break
        static.append(part)
    if not static:
        raise ValueError(f"记录类 {kind!r} 落点模板无静态目录前缀: {record_location(kind, transitions_schema)!r}")
    return Path(*static)


def record_dir(repo_root: Path, kind: str, key: str | None = None, transitions_schema: dict[str, Any] | None = None) -> Path:
    """记录类 kind 的落点目录(绝对 = repo_root/…)。key = 目录占位值(卡 ID 等, 按 validate_safe_task_id 校验);
    key=None = 该类根目录(record_root)。模板目录无占位而给了 key / 有占位而缺 key 由调用方决定: 无占位时 key 被拒(ValueError),
    有占位且 key=None 返回根目录(扫描整类)。路径包含校验: 结果必须落在该类根目录内。"""
    base = Path(repo_root)
    root = base / record_root(kind, transitions_schema)
    if key is None:
        return root
    dir_parts, _filename = _split_location(record_location(kind, transitions_schema))
    placeholders = [part for part in dir_parts if _PLACEHOLDER_RE.search(part)]
    if not placeholders:
        raise ValueError(f"记录类 {kind!r} 落点目录无占位(平铺), 不接受 key={key!r}")
    validate_safe_task_id(key)
    filled = [_PLACEHOLDER_RE.sub(key, part) for part in dir_parts]
    path = base / Path(*filled)
    if not _resolved_within(root, path):
        raise ValueError(f"Record path resolves outside declared records dir {root}: {path}")
    return path


# AIPOS-F109 件①: 记录根(治理目录树 records 键的落点)仅供「扫整个 records/」的读取方(state_lint 记录目录存在性);
# 由声明推导(任一记录类根目录的父目录), 不写死。
def records_root(transitions_schema: dict[str, Any] | None = None) -> Path:
    return record_root("claims", transitions_schema).parent

# AIPOS-F73E(顺手实撞): closure_id/文件名前缀——写(board_adapter.close_task)读(next_resolver._read_task_records)同源。
# 门实际落盘 close_<task>_<ts>_<actor>.md(存量 F73C2/F78 记录皆如此); 读侧曾按 closure_ 找 → 真门 close 后 loop 永看不到闭环记录。
CLOSURE_ID_PREFIX = "close"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    return value


def actor_slug(actor: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", actor.lower()).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    if not value:
        raise ValueError(f"Actor cannot be converted to a safe slug: {actor}")
    return value


def validate_safe_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id or not TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError(f"Unsafe task_id for records path: {task_id}")
    if task_id in {".", ".."} or "/" in task_id or "\\" in task_id or ".." in task_id:
        raise ValueError(f"Unsafe task_id for records path: {task_id}")


def build_runtime_id(prefix: str, task_id: str, timestamp: str, actor: str) -> str:
    validate_safe_task_id(task_id)
    return f"{prefix}_{task_id}_{file_slug("compact", timestamp)}_{actor_slug(actor)}"


def _resolved_within(base_dir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(base_dir.resolve())
        return True
    except ValueError:
        return False


def ensure_safe_record_path(repo_root: Path, path: Path, record_type: str, task_id: str) -> Path:
    validate_safe_task_id(task_id)
    kind = {
        RecordType.CLAIM_LOG: "claims",
        RecordType.SESSION_RECORD: "sessions",
        RecordType.RETURN_RECORD: "returns",
        RecordType.AUDIT_DISPATCH_RECORD: "audit_dispatches",
        RecordType.AUDIT_VERDICT_RECORD: "audit_verdicts",
        RecordType.CLOSURE_RECORD: "closures",
    }.get(record_type)
    if kind is None:
        raise ValueError(f"Unsupported record_type: {record_type}")
    root = record_dir(repo_root, kind, task_id).resolve()
    resolved = path.resolve()
    if not _resolved_within(root, resolved):
        raise ValueError(f"Record path resolves outside allowed records root: {path}")
    if resolved.suffix.lower() != ".md":
        raise ValueError(f"Record path is not a markdown file: {path}")
    return resolved


# AIPOS-F100 件①: 双引号标量里必须转义的字符 = YAML 不可打印字符(PyYAML Reader.NON_PRINTABLE, 读侧 frontmatter 同表)
# + YAML 1.1 换行符(\x85 \u2028 \u2029, 原样写出会被读侧当换行折叠) + 引号/反斜杠。其余字符原样写出(含中文/emoji)。
_DQ_MUST_ESCAPE_RE = re.compile('[^\x20-\x7E\xA0-\u2027\u202A-\uD7FF\uE000-\uFFFD\U00010000-\U0010FFFF]|["\\\\]')
_DQ_SHORT_ESCAPES = {"\0": "\\0", "\x07": "\\a", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\x0b": "\\v", "\x0c": "\\f",
                     "\r": "\\r", "\x1b": "\\e", '"': '\\"', "\\": "\\\\", "\x85": "\\N", "\u2028": "\\L", "\u2029": "\\P"}
# 明标量形式写出的键: 只含这些字符且读回仍是同一字符串(不被 YAML 1.1 解析成 bool/null/数/时间戳)时才不加引号
_PLAIN_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


def _dq_escape_char(match: "re.Match[str]") -> str:
    ch = match.group()
    if ch in _DQ_SHORT_ESCAPES:
        return _DQ_SHORT_ESCAPES[ch]
    code = ord(ch)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _stdlib_yaml_scalar(value: Any) -> str:
    """stdlib YAML 标量写出(AIPOS-F22B → F100): 字符串一律双引号, 只转义 YAML 必须转义的字符(见 _DQ_MUST_ESCAPE_RE);
    bool/null/int 用 YAML 1.1 明标量; float 与 yaml.safe_dump 同形(.inf/.nan, 指数形补 .0, 否则会被读成字符串)。
    其他类型(tuple/set/bytes/对象等)不猜, ValueError 拒写——与 safe_dump 路径同样过不了写后回读校验。"""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        if value != value:
            return ".nan"
        if value in (float("inf"), float("-inf")):
            return ".inf" if value > 0 else "-.inf"
        text = repr(value).lower()
        if "." not in text and "e" in text:
            text = text.replace("e", ".0e", 1)
        return text
    if isinstance(value, str):
        return '"' + _DQ_MUST_ESCAPE_RE.sub(_dq_escape_char, value) + '"'
    raise ValueError(f"stdlib YAML 写出器不支持的值类型 {type(value).__name__}: {value!r}; 拒写")


def _stdlib_yaml_key(key: Any) -> str:
    """映射键: 安全的字符串键写明标量(与 safe_dump 同形, 如 ``task_id``), 其余字符串键写双引号; 非字符串键写其标量形式。"""
    if isinstance(key, str):
        if _PLAIN_KEY_RE.match(key) and _resolve_plain(key, 0, key) == key:
            return key
        return _stdlib_yaml_scalar(key)
    if isinstance(key, (bool, int, float)) or key is None:
        return _stdlib_yaml_scalar(key)
    raise ValueError(f"stdlib YAML 写出器不支持的映射键类型 {type(key).__name__}: {key!r}; 拒写")


def _stdlib_emit_mapping(mapping: dict[Any, Any], indent: int, lines: list[str], first_prefix: str | None = None) -> None:
    """块映射。first_prefix 非 None 时首个键写在该前缀之后(紧凑序列项 ``- key: v``), 其余键缩进到 indent。"""
    pad = " " * indent
    for n, (key, value) in enumerate(mapping.items()):
        lead = (first_prefix if (n == 0 and first_prefix is not None) else pad) + _stdlib_yaml_key(key) + ":"
        _stdlib_emit_value(value, indent, lead, lines, in_mapping=True)


def _stdlib_emit_sequence(items: list[Any], indent: int, lines: list[str]) -> None:
    """块序列, 每项 ``- `` 起于 indent 列(映射下的序列与 safe_dump 同为无缩进形, 由调用方传入键的列)。"""
    pad = " " * indent
    for item in items:
        if isinstance(item, dict) and item:
            _stdlib_emit_mapping(item, indent + 2, lines, first_prefix=pad + "- ")
        elif isinstance(item, list) and item:
            lines.append(pad + "-")
            _stdlib_emit_sequence(item, indent + 2, lines)
        else:
            _stdlib_emit_value(item, indent, pad + "-", lines, in_mapping=False)


def _stdlib_emit_value(value: Any, indent: int, lead: str, lines: list[str], *, in_mapping: bool) -> None:
    """lead = ``<缩进>key:`` 或 ``<缩进>-``; 标量/空集合写在同一行, 非空集合另起块。"""
    if isinstance(value, dict):
        if not value:
            lines.append(lead + " {}")
            return
        lines.append(lead)
        _stdlib_emit_mapping(value, indent + 2, lines)
        return
    if isinstance(value, list):
        if not value:
            lines.append(lead + " []")
            return
        lines.append(lead)
        # 映射下的序列: 无缩进(safe_dump 同形, ``- `` 与键同列); 序列下的序列由 _stdlib_emit_sequence 处理
        _stdlib_emit_sequence(value, indent if in_mapping else indent + 2, lines)
        return
    lines.append(lead + " " + _stdlib_yaml_scalar(value))


def _self_check_yaml(yaml_text: str, ordered_meta: dict[str, Any]) -> None:
    """AIPOS-F46 末道自检: 渲染后 safe_load 回读, 失败即报错拒写, 禁落坏卡.

    验证:
    1. safe_load 能解析(不抛异常)
    2. 解析结果是 dict
    3. 关键标量值 roundtrip 一致(字符串值不被类型误判)
    """
    try:
        if yaml is not None:
            parsed = yaml.safe_load(yaml_text)
        else:
            from tools.aipos_cli.frontmatter import _fallback_parse
            parsed, _warnings = _fallback_parse(yaml_text)
    except Exception as exc:
        raise ValueError(
            f"AIPOS-F46 self-check FAIL: rendered YAML is unparseable: {exc}. "
            f"This means a poison field (e.g. **bold**:colon\"quote) was not properly "
            f"escaped. Refusing to write bad card."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"AIPOS-F46 self-check FAIL: rendered YAML parsed to {type(parsed).__name__}, "
            f"expected dict. Refusing to write bad card."
        )
    # Spot-check: string values must roundtrip as strings (not be coerced to bool/int/null)
    for key, original_value in ordered_meta.items():
        if isinstance(original_value, str) and original_value:
            parsed_value = parsed.get(key)
            if not isinstance(parsed_value, str):
                raise ValueError(
                    f"AIPOS-F46 self-check FAIL: field '{key}' was string {original_value!r} "
                    f"but roundtripped as {type(parsed_value).__name__} ({parsed_value!r}). "
                    f"Refusing to write bad card."
                )


class FrontmatterRoundtripError(ValueError):
    """AIPOS-F87 件①: 写后回读校验失败 —— 渲染出的文本经产品读取口回读后与待写值不逐字一致, 拒写(不落盘)。"""


def _ordered_frontmatter(metadata: dict[str, Any], order: list[str] | None) -> dict[str, Any]:
    ordered_keys = [key for key in (order or []) if key in metadata]
    ordered_keys.extend(sorted(key for key in metadata if key not in ordered_keys))
    # 构建保持插入顺序的 dict (Python 3.7+ dict 有序)
    return {key: _normalize_value(metadata[key]) for key in ordered_keys}


# AIPOS-F115 件④(gap #64): YAML 1.1 换行符 NEL/LS/PS。PyYAML safe_dump 默认把含它们的串写成单引号/明标量并原样输出该字符,
# 读侧按换行折叠 → 写后回读不一致 → 拒写; 而 stdlib 回退路径按双引号转义(\N \L \P)写出, 回读逐字一致 → 能写。两路不对称。
# 统一判据 = 两路同写: 这些值在 YAML 双引号标量里可无损表示(规范转义, 读侧 PyYAML 与零依赖解析器均还原), 拒写只会丢合法数据;
# 故 PyYAML 路径对含这些字符的串强制双引号风格(PyYAML 双引号写出对其按 \N \L \P 转义), 与 stdlib 路径(_DQ_SHORT_ESCAPES)同形。
_YAML_LINE_BREAK_CHARS = ("\x85", "\u2028", "\u2029")
_FRONTMATTER_DUMPER: Any = None


def _frontmatter_safe_dumper() -> Any:
    """SafeDumper 子类(只改 str 表示: 含 NEL/LS/PS 时强制双引号), 其余行为与 yaml.safe_dump 完全一致。"""
    global _FRONTMATTER_DUMPER
    if _FRONTMATTER_DUMPER is None:

        class _FrontmatterDumper(yaml.SafeDumper):  # type: ignore[name-defined,misc]
            pass

        def _represent_str(dumper: Any, data: str) -> Any:
            if any(ch in data for ch in _YAML_LINE_BREAK_CHARS):
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
            return yaml.SafeDumper.represent_str(dumper, data)  # type: ignore[union-attr]

        _FrontmatterDumper.add_representer(str, _represent_str)
        _FRONTMATTER_DUMPER = _FrontmatterDumper
    return _FRONTMATTER_DUMPER


def _dump_frontmatter_yaml(ordered_meta: dict[str, Any]) -> str:
    """frontmatter YAML 文本(不含 --- 围栏)的唯一序列化实现(AIPOS-F22B/F46/F87 单源)。

    主路径 yaml.safe_dump; PyYAML 缺席时走 stdlib 回退(zerodep 核心)。卡与记录的 frontmatter 一律经此, 禁逐行拼接写值。
    """
    if yaml is not None:
        yaml_text = yaml.dump(
            ordered_meta,
            Dumper=_frontmatter_safe_dumper(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=1000000,  # 禁换行
        )
        # safe_dump 输出末尾有换行, 去除后再拼接
        return yaml_text.rstrip("\n")

    # stdlib 回退(zerodep 核心, AIPOS-F100 件①): 递归写块 YAML, 形状集 = 读侧 frontmatter 兜底解析器支持集
    # (任意深度映射/序列、映射内无缩进序列、紧凑序列项 ``- key: v``、空 []/{}、双引号标量)。
    lines: list[str] = []
    _stdlib_emit_mapping(ordered_meta, 0, lines)
    return "\n".join(lines)


def _verify_frontmatter_roundtrip(rendered: str, ordered_meta: dict[str, Any]) -> None:
    """AIPOS-F87 件① 写后回读校验: 把**即将落盘的完整文本**交给产品唯一读取口 parse_markdown_frontmatter 回读,
    要求无解析告警、且全部字段值与待写值逐字一致(类型 + 值)。不一致 = FrontmatterRoundtripError, 调用方不落盘。

    在写盘之前对同一字节串校验, 等价于「写后回读」且失败时不留坏文件(fail-closed, 不吞)。
    """
    parsed, _body, warnings = parse_markdown_frontmatter(rendered)
    if warnings:
        raise FrontmatterRoundtripError(
            f"AIPOS-F87 写后回读校验失败: 产品读取口解析告警 {warnings}; 拒写(不落盘)"
        )
    if parsed != ordered_meta:
        diffs = [
            key
            for key in sorted(set(parsed) | set(ordered_meta), key=str)
            if parsed.get(key, object()) != ordered_meta.get(key, object())
        ]
        raise FrontmatterRoundtripError(
            f"AIPOS-F87 写后回读校验失败: 字段 {diffs} 回读值与待写值不一致; 拒写(不落盘)"
        )


def render_frontmatter_block(metadata: dict[str, Any], order: list[str] | None = None) -> str:
    """只渲染 frontmatter 块 ``---\\n<yaml>\\n---``(无正文、无末尾换行), 与 render_markdown 同一序列化与回读校验。

    供「frontmatter 之后自行拼正文」的写入点使用(决策条目骨架 / RETURN 骨架 / 草稿记录头), 禁再逐行拼 key: value。
    """
    ordered_meta = _ordered_frontmatter(metadata, order)
    yaml_text = _dump_frontmatter_yaml(ordered_meta)
    _self_check_yaml(yaml_text, ordered_meta)
    block = f"---\n{yaml_text}\n---"
    _verify_frontmatter_roundtrip(block + "\n", ordered_meta)
    return block


def render_markdown(metadata: dict[str, Any], body: str, order: list[str] | None = None) -> str:
    """Render markdown with YAML frontmatter.

    AIPOS-F22B: frontmatter 一律经 YAML 序列化器 (safe_dump 或等价) 输出, 禁字符串拼接.
    使用 yaml.safe_dump (PyYAML 可用时) 或 stdlib fallback.

    AIPOS-F46: 末道自检——渲染后 safe_load 回读, 失败即报错拒写, 禁落坏卡.
    AIPOS-F87 件①: 再以产品读取口回读完整文本, 全部字段逐字还原才放行(_verify_frontmatter_roundtrip)。
    字段顺序: order 内的键按 order, 其余按字母序(既有格式, 避免全量重排)。
    """
    ordered_meta = _ordered_frontmatter(metadata, order)
    yaml_text = _dump_frontmatter_yaml(ordered_meta)
    # AIPOS-F46 末道自检(F87 前 stdlib 回退分支误截掉末两行再自检, 现两路同一文本)
    _self_check_yaml(yaml_text, ordered_meta)
    rendered = f"---\n{yaml_text}\n---\n{body.rstrip()}\n"
    _verify_frontmatter_roundtrip(rendered, ordered_meta)
    return rendered


def render_frontmatter_line(key: str, value: Any) -> str:
    """AIPOS-F87 件②: 单个顶层标量字段的规整行(``key: <安全标量>``), 与 render_markdown 同一序列化。

    state repair 对坏卡做保值规整时只重写解析失败的那一行, 用本函数产出该行(禁手写引号化)。产出非单行 = ValueError。
    """
    block = render_frontmatter_block({key: value}, [key])
    lines = block.split("\n")
    if len(lines) != 3:
        raise ValueError(f"字段 {key} 的安全序列化不是单行(值含换行等), 不能做单行规整")
    return lines[1]

# AIPOS-F87 件①(复查报告 M9): 卡 frontmatter 字段序原有两份(queue_mutation 42 键 / draft_writer 28 键), F87 收为本模块一份。
# AIPOS-F108 件①(M9, Owner 10-02 裁定 card.schema 字段序与落盘序一致): 字段序与字段缺省值的唯一声明在
# schema/card.schema.json(frontmatter_order.keys / fields.<键>.default); 本模块只投影, 代码零清单。
# 列外的键按字母序排在其后(render_markdown 既有规则)。声明缺/坏 = SchemaLoadError(fail-closed, 不回落写死)。


def _card_schema_declaration() -> dict[str, Any]:
    from tools.schema_loader import SchemaLoadError, load_schema

    decl = load_schema("card")
    fields = decl.get("fields")
    order = (decl.get("frontmatter_order") or {}).get("keys")
    if not isinstance(fields, dict) or not fields:
        raise SchemaLoadError("card.schema.json fields 未声明")
    if not isinstance(order, list) or not order or not all(isinstance(k, str) and k for k in order):
        raise SchemaLoadError("card.schema.json frontmatter_order.keys 未声明或非字符串清单")
    undeclared = [k for k in order if k not in fields]
    if undeclared or len(set(order)) != len(order):
        raise SchemaLoadError(f"card.schema.json frontmatter_order.keys 含未在 fields 声明或重复的键: {undeclared or order}")
    return decl


def card_field_defaults() -> dict[str, Any]:
    """AIPOS-F108 件①: 卡字段缺省值 = card.schema fields.<键>.default 的投影(唯一来源; 草稿模板 / 派生卡补全都读这里)。"""
    fields = _card_schema_declaration()["fields"]
    return {key: spec["default"] for key, spec in fields.items() if isinstance(spec, dict) and "default" in spec}


CARD_FRONTMATTER_ORDER = list(_card_schema_declaration()["frontmatter_order"]["keys"])


CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "claim_id",
    "task_id",
    "task_path",
    "actor",
    "claim_action",
    "created_at",
    "from_state",
    "to_state",
    "session_id",
]

SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "actor",
    "created_at",
    "updated_at",
    "status",
    "claim_id",
    "current_state",
    "event_count",
]

MCP_CLAIM_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "claim_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "actual_model",
    "reported_tokens",
    "claimed_at",
    "from_state",
    "to_state",
    "claim_policy",
    "claim_match_basis",
    "claim_requirements_hash",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "submitted_by",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "session_id",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_SESSION_FRONTMATTER_ORDER = [
    "record_type",
    "session_id",
    "task_id",
    "task_path",
    "surface",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "claim_id",
    "created_at",
    "updated_at",
    "session_status",
    "current_state",
    "lease_status",
    "lease_path",
    "active_lease_written",
    "event_count",
]

MCP_RETURN_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "return_id",
    "task_id",
    "task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "actual_model",
    "reported_tokens",
    "agent_runtime",
    "claim_id",
    "session_id",
    "returned_at",
    "executor_status",
    "audit_readiness",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "result_summary_present",
    "artifact_refs",
    "completion_report_ref",
    "artifact_subject",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "confirmer_role",
    "confirmer_token_ref",
    "confirmer_token_fingerprint",
    "submitted_by",
    "gate_signature",
    "authority_seal",
    "signature_key_ref",
    "signed_payload_hash",
    "signed_at",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "dispatch_id",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "reviewed_executor_instance",
    "reviewed_executor_claim_id",
    "reviewed_executor_session_id",
    "audit_task_id",
    "audit_task_path",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "dispatched_at",
    "independence_distinct_instance",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "dependency_executor_status",
    "dependency_audit_readiness",
    "dependency_audit_status",
    "lease_status",
    "lease_path",
    "active_lease_written",
]

MCP_AUDIT_VERDICT_FRONTMATTER_ORDER = [
    "record_type",
    "event_type",
    "verdict_id",
    "verdict",
    "reviewed_task_id",
    "reviewed_task_path",
    "reviewed_return_record_ref",
    "audit_dispatch_record_ref",
    "audit_provenance_type",
    "audit_task_id",
    "audit_task_path",
    "audit_claim_id",
    "audit_session_id",
    "reviewed_executor_instance",
    "auditor_instance",
    "independence_distinct_instance",
    "surface",
    "operation",
    "autonomy_mode",
    "actor",
    "canonical_agent_instance",
    "owner_policy_ref",
    "agent_runtime",
    "verdict_at",
    "findings_summary_present",
    "evidence_refs",
    "recommended_next_action",
    "dry_run_id",
    "dry_run_snapshot_hash",
    "confirmation_ref",
    "submitted_by",
    "dependency_audit_status_after",
    "finalize_performed",
    "accepted_work_unblocked",
    "lease_status",
    "lease_path",
    "active_lease_written",
]


def build_claim_log_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    claim_id: str,
    session_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": RecordType.CLAIM_LOG,
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "claim_action": "claimed",
        "created_at": created_at,
        "from_state": "pending",
        "to_state": "claimed",
        "session_id": session_id,
    }
    body = "\n".join(
        [
            f"# Claim Log: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` claimed by `{actor}`.",
            "",
            "## Safety",
            "",
            "This claim log was created by AIPOS queue mutation with records enabled.",
            "",
        ]
    )
    return render_markdown(metadata, body, CLAIM_FRONTMATTER_ORDER)


def build_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    session_id: str,
    claim_id: str,
    created_at: str,
) -> str:
    metadata = {
        "record_type": RecordType.SESSION_RECORD,
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "actor": actor,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "active",
        "claim_id": claim_id,
        "current_state": "claimed",
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} claimed by {actor}",
            "",
        ]
    )
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def _confirmer_fields(confirmer: dict[str, Any] | None) -> dict[str, Any]:
    """AIPOS-197 confirmer attribution + AIPOS-193 §9 signature-ready placeholders.

    Records WHO confirmed (role + non-secret token fingerprint) so L3 can tell an
    Owner-role confirmation from an agent self-confirmation. Never stores a raw token.
    """
    c = confirmer or {}
    return {
        "confirmer_role": str(c.get("confirmer_role") or ""),
        "confirmer_token_ref": str(c.get("confirmer_token_ref") or ""),
        "confirmer_token_fingerprint": str(c.get("confirmer_token_fingerprint") or ""),
        # AIPOS-F73E 件②: 提交身份(驱动方 token 角色实例)只记不判; actor 仍=认领实例(transitions record_authenticity.submission_identity)
        "submitted_by": str(c.get("submitted_by") or ""),
        "gate_signature": "",
        "authority_seal": "",
        "signature_key_ref": "",
        "signed_payload_hash": "",
        "signed_at": "",
    }


def build_mcp_claim_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    claim_id: str,
    session_id: str,
    claimed_at: str,
    autonomy_mode: str = "Supervised",
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    claim_policy: str | None = None,
    claim_match_basis: str | None = None,
    claim_requirements_hash: str | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.CLAIM_RECORD,
        "event_type": "mcp_queue_claim",
        "claim_id": claim_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_claim",
        # AIPOS-250: autonomy_mode is now read from the caller (Supervised | PreAuthorized),
        # no longer hardcoded — a PreAuthorized envelope auto-release stamps PreAuthorized so the
        # record self-attributes to the policy that permitted it.
        "autonomy_mode": str(autonomy_mode or "Supervised").strip() or "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        # AIPOS-250 (capability ledger): agent-REPORTED, not gate-measured (disclosure #15).
        "actual_model": str(actual_model or "").strip(),
        "reported_tokens": int(reported_tokens) if isinstance(reported_tokens, int) else "",
        "claimed_at": claimed_at,
        "from_state": "pending",
        "to_state": "claimed",
        "claim_policy": claim_policy or "",
        "claim_match_basis": claim_match_basis or "",
        "claim_requirements_hash": claim_requirements_hash or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "session_id": session_id,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    body = "\n".join(
        [
            f"# MCP Claim Record: {claim_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was claimed by `{canonical_agent_instance}` through the {metadata['autonomy_mode']} MCP claim surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            "",
            "## Boundary",
            "",
            "This record is provenance evidence only. It does not activate a lease, launch work, dispatch audit, record audit PASS, finalize, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_CLAIM_FRONTMATTER_ORDER)


def build_mcp_claim_session_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    session_id: str,
    claim_id: str,
    created_at: str,
    autonomy_mode: str = "Supervised",
) -> str:
    metadata = {
        "record_type": RecordType.SESSION_RECORD,
        "session_id": session_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        # AIPOS-250: the session is a claim-side artifact — reflect the claim's autonomy_mode
        # (Supervised | PreAuthorized) so it stays consistent with the claim record.
        "autonomy_mode": str(autonomy_mode or "Supervised").strip() or "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "claim_id": claim_id,
        "created_at": created_at,
        "updated_at": created_at,
        "session_status": "claimed",
        "current_state": "claimed",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
        "event_count": 1,
    }
    body = "\n".join(
        [
            f"# MCP Session Record: {session_id}",
            "",
            "## Events",
            "",
            f"- {created_at} mcp_queue_claim by {canonical_agent_instance}; claim_id={claim_id}; owner_policy_ref={owner_policy_ref}; lease_status=proposed.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def build_mcp_return_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    return_id: str,
    claim_id: str,
    session_id: str,
    returned_at: str,
    result_summary: str | None,
    artifact_refs: list[str],
    completion_report_ref: str | None,
    actual_model: str | None = None,
    reported_tokens: int | None = None,
    agent_runtime: dict[str, Any] | None = None,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    confirmer: dict[str, Any] | None = None,
    self_check_waived: bool = False,
    self_check_waiver_reason: str | None = None,
    artifact_subject: dict[str, Any] | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.RETURN_RECORD,
        "event_type": "mcp_queue_return",
        "return_id": return_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_return",
        # AIPOS-250 red line 4: return stays Supervised-only (per-task owner_confirm); the
        # PreAuthorized tier is CLAIM-only this slice. Do NOT parametrize this.
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        # AIPOS-265 (field convergence): agent_runtime is the single new runtime 口径.
        # Legacy actual_model/reported_tokens are persisted ONLY when the caller still
        # sends a value (conditional below); empty values are dropped so new return
        # records no longer carry dead 空 fields. History files are never rewritten.
        "claim_id": claim_id,
        "session_id": session_id,
        "returned_at": returned_at,
        "executor_status": "completed",
        "audit_readiness": "ready",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "result_summary_present": bool(result_summary),
        "artifact_refs": artifact_refs,
        "completion_report_ref": completion_report_ref or "",
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        **_confirmer_fields(confirmer),
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-265 (field convergence): legacy actual_model/reported_tokens persisted ONLY
    # when non-empty (空置停写). agent_runtime (below) is the single new 口径; these
    # stay for read-side compat with callers still sending a value. Frontmatter order
    # keeps them adjacent to agent_runtime when present, absent otherwise.
    _actual_model = str(actual_model or "").strip()
    if _actual_model:
        metadata["actual_model"] = _actual_model
    if isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool):
        metadata["reported_tokens"] = reported_tokens
    # AIPOS-261 (additive): only persist agent_runtime when at least one sub-value is
    # present, so old records (and returns that did not report runtime) simply lack the
    # key — the popup reads absent-key as 未记录.
    if isinstance(agent_runtime, dict) and agent_runtime:
        metadata["agent_runtime"] = dict(agent_runtime)
    # AIPOS-F114 件①: 门交回记录绑定被交回产物(卡分支此刻 tip; next_resolver.return_binding_subject), 交回过期判据读它;
    # 非代码卡/不可解析时不带(与存量记录同形)
    if isinstance(artifact_subject, dict) and str(artifact_subject.get("commit_sha") or "").strip():
        metadata["artifact_subject"] = dict(artifact_subject)
    
    # AIPOS-F49-fix1: self_check_waived 标记（Owner 强制放行）
    if self_check_waived:
        metadata["self_check_waived"] = True
        if self_check_waiver_reason:
            metadata["self_check_waiver_reason"] = self_check_waiver_reason
    body = "\n".join(
        [
            f"# MCP Return Record: {return_id}",
            "",
            "## Summary",
            "",
            f"- Task `{task_id}` was returned by `{canonical_agent_instance}` through the Supervised MCP return surface.",
            f"- Owner policy: `{owner_policy_ref}`.",
            f"- Result summary: {result_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record marks executor completion plus audit readiness only. It does not dispatch audit, record audit PASS, finalize, activate a lease, or unblock dependent work.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_RETURN_FRONTMATTER_ORDER)


def build_mcp_audit_dispatch_record_markdown(
    *,
    dispatch_id: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    reviewed_executor_instance: str,
    reviewed_executor_claim_id: str,
    reviewed_executor_session_id: str,
    audit_task_id: str,
    audit_task_path: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    dispatched_at: str,
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    supersedes: str | None = None,
) -> str:
    metadata = {
        "record_type": RecordType.AUDIT_DISPATCH_RECORD,
        "event_type": "mcp_audit_dispatch",
        "dispatch_id": dispatch_id,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "reviewed_executor_instance": reviewed_executor_instance,
        "reviewed_executor_claim_id": reviewed_executor_claim_id,
        "reviewed_executor_session_id": reviewed_executor_session_id,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "surface": "mcp",
        "operation": RecordType.AUDIT_DISPATCH,
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "dispatched_at": dispatched_at,
        "independence_distinct_instance": True,
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "dependency_executor_status": "completed",
        "dependency_audit_readiness": "ready",
        "dependency_audit_status": "pending",
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-F72: supersedes 引用(放行 re-dispatch 时记录旧链)
    if supersedes:
        metadata["supersedes"] = supersedes
    
    body_lines = [
        f"# MCP Audit Dispatch Record: {dispatch_id}",
        "",
        "## Summary",
        "",
        f"- Task `{reviewed_task_id}` was dispatched for independent audit as `{audit_task_id}`.",
        f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
        f"- Owner policy: `{owner_policy_ref}`.",
    ]
    if supersedes:
        body_lines.extend([
            "",
            "## Re-dispatch Note (AIPOS-F72)",
            "",
            f"This dispatch supersedes a prior dead dispatch chain: `{supersedes}`.",
            "The previous audit card was concluded with zero verdicts (e.g., 'no substance to audit').",
        ])
    body_lines.extend([
        "",
        "## Boundary",
        "",
        "This record creates audit-dispatch provenance only. It does not claim the audit task, launch an auditor, record a verdict, finalize, activate a lease, or unblock dependent work.",
        "",
    ])
    body = "\n".join(body_lines)
    return render_markdown(metadata, body, MCP_AUDIT_DISPATCH_FRONTMATTER_ORDER)


def build_mcp_audit_verdict_record_markdown(
    *,
    verdict_id: str,
    verdict: str,
    reviewed_task_id: str,
    reviewed_task_path: str,
    reviewed_return_record_ref: str,
    audit_dispatch_record_ref: str,
    audit_provenance_type: str = "dispatch",
    audit_task_id: str,
    audit_task_path: str,
    audit_claim_id: str,
    audit_session_id: str,
    reviewed_executor_instance: str,
    auditor_instance: str,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    verdict_at: str,
    findings_summary: str | None,
    evidence_refs: list[str],
    recommended_next_action: str | None,
    owner_waiver_ref: str | None = None,  # AIPOS-R6A 靶子④: 仲裁/豁免一等公民
    dry_run_id: str | None = None,
    dry_run_snapshot_hash: str | None = None,
    confirmation_ref: str | None = None,
    agent_runtime: dict[str, Any] | None = None,
    artifact_subject: dict[str, Any] | None = None,  # AIPOS-F70: 产物指纹
    submitted_by: str | None = None,  # AIPOS-F73E 件②: 提交身份(驱动方 token 实例), 只记不判
    report_snapshot: dict[str, Any] | None = None,  # AIPOS-F90 件②: 审计报告快照指针(transitions artifact_ingest.verdict.report_snapshot)
) -> str:
    metadata = {
        "record_type": RecordType.AUDIT_VERDICT_RECORD,
        "event_type": "mcp_audit_verdict",
        "verdict_id": verdict_id,
        "verdict": verdict,
        "reviewed_task_id": reviewed_task_id,
        "reviewed_task_path": reviewed_task_path,
        "reviewed_return_record_ref": reviewed_return_record_ref,
        "audit_dispatch_record_ref": audit_dispatch_record_ref,
        "audit_provenance_type": audit_provenance_type,
        "audit_task_id": audit_task_id,
        "audit_task_path": audit_task_path,
        "audit_claim_id": audit_claim_id,
        "audit_session_id": audit_session_id,
        "reviewed_executor_instance": reviewed_executor_instance,
        "auditor_instance": auditor_instance,
        "independence_distinct_instance": auditor_instance != reviewed_executor_instance,
        "surface": "mcp",
        "operation": RecordType.AUDIT_VERDICT,
        "autonomy_mode": "Supervised",
        "actor": actor,
        "canonical_agent_instance": canonical_agent_instance,
        "owner_policy_ref": owner_policy_ref,
        "verdict_at": verdict_at,
        "findings_summary_present": bool(findings_summary),
        "evidence_refs": evidence_refs,
        "recommended_next_action": recommended_next_action or "",
        "owner_waiver_ref": owner_waiver_ref or "",  # AIPOS-R6A 靶子④: 仲裁/豁免引用
        "dry_run_id": dry_run_id or "",
        "dry_run_snapshot_hash": dry_run_snapshot_hash or "",
        "confirmation_ref": confirmation_ref or "",
        "submitted_by": str(submitted_by or ""),
        "dependency_audit_status_after": Verdict.PASS if verdict == Verdict.PASS else verdict,
        "finalize_performed": False,
        "accepted_work_unblocked": False,
        "lease_status": "proposed",
        "lease_path": "claim_only",
        "active_lease_written": False,
    }
    # AIPOS-265 FIX-1 (additive, symmetric to the return half): only persist
    # agent_runtime when at least one sub-value is present, so verdict records that
    # did not report runtime simply lack the key — the 档案 popup reads absent-key
    # as 未记录, and existing verdict tests/frontmatter stay byte-identical.
    if isinstance(agent_runtime, dict) and agent_runtime:
        metadata["agent_runtime"] = dict(agent_runtime)
    # AIPOS-F70: artifact_subject 写入裁决记录 (只在提供时写入, 存量裁决无此字段)
    # 新裁决: task_mode=code 的被审卡必须提供 (gate 侧已 fail-closed 校验)
    # 存量裁决: 无此字段 -> finalize/deploy 以警告放行并标注 legacy-verdict
    if isinstance(artifact_subject, dict) and artifact_subject:
        metadata["artifact_subject"] = dict(artifact_subject)
    # AIPOS-F90 件②: 裁决入门时门快照的审计报告(只在有报告时写, 存量/无报告裁决无此三字段)
    if isinstance(report_snapshot, dict) and report_snapshot:
        metadata["report_snapshot_ref"] = str(report_snapshot.get("path") or "")
        metadata["report_snapshot_sha256"] = str(report_snapshot.get("sha256") or "")
        metadata["report_source_ref"] = str(report_snapshot.get("source_ref") or "")
    body = "\n".join(
        [
            f"# MCP Audit Verdict Record: {verdict_id}",
            "",
            "## Summary",
            "",
            f"- Audit task `{audit_task_id}` returned verdict `{verdict}` for `{reviewed_task_id}`.",
            f"- Auditor instance: `{auditor_instance}`.",
            f"- Reviewed executor instance: `{reviewed_executor_instance}`.",
            f"- Findings summary: {findings_summary or 'not provided'}",
            "",
            "## Boundary",
            "",
            "This record is independent audit evidence. PASS may satisfy audit_pass only. It does not finalize, activate a lease, or unblock accepted-work dependencies.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_AUDIT_VERDICT_FRONTMATTER_ORDER)


def load_session_record(path: Path) -> tuple[dict[str, Any], str, list[str]]:
    text = path.read_text(encoding="utf-8")
    metadata, body, warnings = parse_markdown_frontmatter(text)
    return _normalize_value(metadata), body, warnings


def update_session_record_markdown(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    timestamp: str,
    status: str,
    current_state: str,
    event_line: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["status"] = status
    metadata["current_state"] = current_state
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join([body, "", f"- {event_line}", ""])
    return render_markdown(metadata, body, SESSION_FRONTMATTER_ORDER)


def append_mcp_return_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    return_id: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = "returned"
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_queue_return by {canonical_agent_instance}; return_id={return_id}; owner_policy_ref={owner_policy_ref}; audit_readiness=ready.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def append_mcp_audit_verdict_session_event(
    existing_metadata: dict[str, Any],
    existing_body: str,
    *,
    actor: str,
    canonical_agent_instance: str,
    owner_policy_ref: str,
    timestamp: str,
    verdict_id: str,
    verdict: str,
) -> str:
    metadata = dict(existing_metadata)
    metadata["updated_at"] = timestamp
    metadata["session_status"] = RecordType.AUDIT_VERDICT
    metadata["current_state"] = "claimed"
    metadata.setdefault("lease_status", "proposed")
    metadata.setdefault("lease_path", "claim_only")
    metadata.setdefault("active_lease_written", False)
    current_count = metadata.get("event_count")
    try:
        event_count = int(current_count) if current_count is not None else 0
    except (TypeError, ValueError):
        event_count = 0
    metadata["event_count"] = event_count + 1
    metadata.setdefault("actor", actor)
    metadata.setdefault("canonical_agent_instance", canonical_agent_instance)
    metadata.setdefault("owner_policy_ref", owner_policy_ref)
    metadata.setdefault("record_type", RecordType.SESSION_RECORD)
    body = existing_body.rstrip()
    if "## Events" not in body:
        body = "\n".join([body, "", "## Events"]).strip()
    body = "\n".join(
        [
            body,
            "",
            f"- {timestamp} mcp_audit_verdict by {canonical_agent_instance}; verdict_id={verdict_id}; verdict={verdict}; owner_policy_ref={owner_policy_ref}.",
            "",
        ]
    )
    return render_markdown(metadata, body, MCP_SESSION_FRONTMATTER_ORDER)


def claim_record_paths(repo_root: Path, task_id: str, claim_id: str, session_id: str) -> tuple[Path, Path]:
    claim_path = ensure_safe_record_path(repo_root, expected_claim_log_path(repo_root, task_id, claim_id), RecordType.CLAIM_LOG, task_id)
    session_path = ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), RecordType.SESSION_RECORD, task_id)
    return claim_path, session_path


def session_record_path(repo_root: Path, task_id: str, session_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_session_record_path(repo_root, task_id, session_id), RecordType.SESSION_RECORD, task_id)


def return_record_path(repo_root: Path, task_id: str, return_id: str) -> Path:
    return ensure_safe_record_path(repo_root, expected_return_record_path(repo_root, task_id, return_id), RecordType.RETURN_RECORD, task_id)


def audit_dispatch_record_path(repo_root: Path, task_id: str, dispatch_id: str) -> Path:
    path = record_dir(repo_root, "audit_dispatches", task_id) / f"{dispatch_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.AUDIT_DISPATCH_RECORD, task_id)


def audit_verdict_record_path(repo_root: Path, task_id: str, verdict_id: str) -> Path:
    path = record_dir(repo_root, "audit_verdicts", task_id) / f"{verdict_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.AUDIT_VERDICT_RECORD, task_id)


def closure_record_path(repo_root: Path, task_id: str, closure_id: str) -> Path:
    path = record_dir(repo_root, "closures", task_id) / f"{closure_id}.md"
    return ensure_safe_record_path(repo_root, path, RecordType.CLOSURE_RECORD, task_id)


def build_closure_record_markdown(
    *,
    task_id: str,
    task_path: str,
    actor: str,
    closure_id: str,
    closed_at: str,
    closure_evidence: dict[str, Any],
    return_record_ref: str | None = None,
    related_audit_task_refs: list[str] | None = None,
    warnings: list[str] | None = None,
    submitted_by: str | None = None,  # AIPOS-F73E 件②: 提交身份(驱动方), actor=认领实例
) -> str:
    """Build a closure record markdown document (AIPOS-283/289).

    The closure record is the append-only proof that a task was formally closed
    (moved from claimed/ to completed/) through the gate's close verb. It records
    who closed it, when, and what evidence justified the closure.

    AIPOS-289: warnings (governance account drift) are written to frontmatter.
    """
    metadata = {
        "record_type": RecordType.CLOSURE_RECORD,
        "event_type": "mcp_queue_close",
        "closure_id": closure_id,
        "task_id": task_id,
        "task_path": task_path,
        "surface": "mcp",
        "operation": "queue_close",
        "actor": actor,
        "submitted_by": str(submitted_by or ""),
        "closed_at": closed_at,
        "closure_evidence_type": closure_evidence.get("type", "unknown"),
        "closure_evidence_ref": closure_evidence.get("ref", ""),
        "return_record_ref": return_record_ref or "",
    }
    if related_audit_task_refs:
        metadata["related_audit_task_refs"] = related_audit_task_refs
    if warnings:
        metadata["warnings"] = warnings
    body = (
        f"# Closure Record: {closure_id}\n\n"
        f"Task `{task_id}` was closed (claimed → completed) by `{actor}` at `{closed_at}`.\n\n"
        f"## Evidence\n\n"
        f"- Type: {closure_evidence.get('type', 'unknown')}\n"
        f"- Ref: {closure_evidence.get('ref', '')}\n\n"
        f"## Return Record\n\n"
        f"- Return record ref: {return_record_ref or 'N/A'}\n"
    )
    if related_audit_task_refs:
        body += f"\n## Related Audit Tasks\n\n"
        for ref in related_audit_task_refs:
            body += f"- {ref}\n"
    if warnings:
        body += f"\n## Warnings\n\n"
        for warning in warnings:
            body += f"- {warning}\n"
    return render_markdown(metadata, body, [
        "record_type", "event_type", "closure_id", "task_id", "task_path",
        "surface", "operation", "actor", "submitted_by", "closed_at", "closure_evidence_type",
        "closure_evidence_ref", "return_record_ref", "related_audit_task_refs",
        "warnings",
    ])
# ---------------------------------------------------------------------------
# AIPOS-F64: 单一记录写入器 (唯一实现点)
#
# 所有记录写入经此函数。调用方只需提供markdown内容,由此函数负责:
# 1. 路径解析 (复用既有 *_record_path 函数)
# 2. 原子写入 (多条记录全落或全不落)
# 3. 路径安全校验
# ---------------------------------------------------------------------------

def _resolve_record_path_from_schema(
    transitions_schema: dict[str, Any],
    repo_root: Path,
    record_type: str,
    record_id: str,
    task_id: str | None,
) -> Path:
    """从 transitions.schema.json 声明中解析记录路径 (AIPOS-F64-fix1 声明驱动; AIPOS-F109 件①: 经唯一读取口 record_dir)。

    记录类型 → 记录类 = record_locations.kinds[*].record_types(record_kind_for_type; 原代码内 type_to_node 映射与 event 特例退役);
    落点目录 = 该类 location 模板的目录部分(节点记录读传入 transitions_schema 的节点 record.location——改声明即跟随),
    占位值 = task_id(卡 ID; 平铺类如 deployments 传 None); 文件名 = record_id + ".md"。
    Raises: ValueError(类型未声明 / 落点未声明 / 不安全 id / 越出声明目录)。
    """
    kind = record_kind_for_type(record_type)
    # AIPOS-F79D 件④: 路径包含校验(fail-closed, 不再依赖事后 ensure_safe_record_path + 吞异常):
    # task_id/record_id 不得含路径分隔或 `..`, 解析结果必须落在声明目录内。
    if "/" in record_id or "\\" in record_id or ".." in record_id or not record_id:
        raise ValueError(f"Unsafe record_id for records path: {record_id!r}")
    dir_parts, _filename = _split_location(record_location(kind, transitions_schema))
    has_placeholder = any(_PLACEHOLDER_RE.search(part) for part in dir_parts)
    if has_placeholder and task_id is None:
        raise ValueError(f"记录类 {kind} 落点目录含占位, 缺 key(卡 ID): {record_id}")
    declared_dir = record_dir(repo_root, kind, task_id if has_placeholder else None, transitions_schema)
    path = declared_dir / f"{record_id}.md"
    if not _resolved_within(declared_dir, path):
        raise ValueError(f"Record path resolves outside declared records dir {declared_dir}: {path}")
    return path


def write_records_atomic(
    repo_root: Path,
    records: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """单一记录写入器 (AIPOS-F64 唯一实现点)。
    
    Args:
        repo_root: 工作区根目录
        records: 待写入记录列表,每项为 (record_type, record_id, markdown_content)
    
    Returns:
        dict 包含 ok, paths (写入的路径列表), wrote (是否真正写入)
    
    原子性保证:
        - 多条记录要么全落要么全不落
        - 失败时抛异常,已写入的文件会被清理
    
    声明驱动 (AIPOS-F64 验收③):
        - 路径路由由 transitions.schema.json 声明驱动
        - 改 schema 中记录位置 → writer 行为随之改变,代码零改动
    
    示例:
        records = [
            ("claim", "claim_TASK-1_20260902_120000_agent", claim_markdown),
            ("session", "session_TASK-1_20260902_120000", session_markdown),
        ]
        result = write_records_atomic(repo_root, records)
    """
    if not records:
        return {"ok": True, "wrote": False, "paths": [], "record_count": 0}
    
    # AIPOS-F64-fix1: 声明驱动路径解析
    from tools.schema_loader import load_schema
    
    # 尝试从 repo_root 加载 schema (测试场景);如果不存在则使用代码仓根 (生产场景)
    schema_path = repo_root / "schema" / "transitions.schema.json"
    if schema_path.exists():
        # 测试场景: 直接读取 tmpdir 中的 schema
        import json
        transitions_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    else:
        # 生产场景: 使用 schema_loader
        from tools.schema_loader import code_repo_schema_root
        schema_root = code_repo_schema_root()
        transitions_schema = load_schema("transitions", schema_root)
    
    # 解析路径 (先全部解析,再统一写入)
    resolved: list[tuple[Path, str]] = []
    for entry in records:
        # AIPOS-F109 件①: 记录项可带第 4 元 key(落点目录占位值 = 卡 ID; 平铺类传 None)——写入器不再从 record_id 猜目录
        # (原「record_id 第二段 = task_id」: 卡 ID 含 `_` 即落错目录、publish 落小写 slug 目录、deployment 拿时间戳当键)。
        # 3 元项沿用旧推导(record_id 第二段), 存量调用方不变。
        if len(entry) == 4:
            record_type, record_id, markdown, task_id = entry
        else:
            record_type, record_id, markdown = entry
            parts = record_id.split("_")
            if len(parts) < 2:
                raise ValueError(f"Invalid record_id format: {record_id}")
            task_id = parts[1]  # 提取task_id
        
        # 标准化record_type (支持字符串和RecordType常量)
        record_type_str = str(record_type).lower()
        normalized_type = record_type_str.replace("_log", "").replace("_record", "")

        # AIPOS-F79D 件④: 0 字节记录禁写(F73ER/F78CR 两案 sessions/ 下留空文件的病根之一是写侧对空内容照写)
        if not str(markdown).strip():
            raise ValueError(f"记录内容为空, 拒写 0 字节记录: {record_type_str} {record_id}")

        if normalized_type == "session":
            # AIPOS-F79D 件④(分叉点根治): session 记录唯一落点 = 声明位 records/sessions/<task_id>/,
            # 与 MCP 两跳认领(board_adapter._mcp_claim_record_plan → claim_record_paths)同一函数 session_record_path。
            # 病根: 旧法把 "session" 映射到 transitions N1 的 claim 记录位置 → 写进 records/claims/<ID>/session_*.md,
            # 且 ensure_safe_record_path 的 ValueError 被 `except ValueError: pass` 吞掉 → 副本静默落错位。
            path = session_record_path(repo_root, task_id, record_id)
        else:
            # 声明驱动路径解析: 从 schema 读取记录配置(路径包含校验在解析函数内, 不吞)
            path = _resolve_record_path_from_schema(
                transitions_schema, repo_root, record_type_str, record_id, task_id
            )
        
        resolved.append((path, markdown))
    
    # 原子写入:全部路径检查通过后才开始写
    written_paths: list[str] = []
    try:
        for path, markdown in resolved:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            written_paths.append(str(path.relative_to(repo_root)))
    except Exception as exc:
        # 失败时尝试清理已写入的文件
        for written_path_str in written_paths:
            try:
                (repo_root / written_path_str).unlink(missing_ok=True)
            except OSError as cleanup_exc:  # AIPOS-F93: 回滚清理失败出声(原 except Exception: pass 静默吞)
                import sys

                print(f"Warning: 记录写入回滚未能删除 {written_path_str}: {cleanup_exc}", file=sys.stderr)
        raise RuntimeError(f"记录写入失败 (已回滚): {exc}") from exc
    
    return {
        "ok": True,
        "wrote": True,
        "paths": written_paths,
        "record_count": len(written_paths),
    }


def _skeleton_frontmatter(contract: list[dict[str, Any]]) -> str:
    """AIPOS-F93 件①: 认领模板 frontmatter = 报告必填契约逐项占位(`(待填写: <说明>)`, 占位判据 next_resolver._is_placeholder_value)。"""
    keys = [str(e["key"]) for e in contract]
    return render_frontmatter_block({str(e["key"]): f"(待填写: {e['hint']})" for e in contract}, keys) + "\n"


def build_return_skeleton_markdown(task_id: str) -> str:
    """Build RETURN.md skeleton with fixed section headings (AIPOS-F65A 大项①).
    
    Claim confirm 时门在声明位创建骨架, 执行体只填不选址。
    骨架含固定节标题, 带占位符提示填写, 被 F60-fix1 isReturnMdSubstantive 检测拦截。
    
    Args:
        task_id: 任务 ID
        
    Returns:
        RETURN.md skeleton markdown content
    """
    # AIPOS-F78 件③: 骨架带必填 frontmatter 占位(键名唯一声明 transitions artifact_ingest.return.required_frontmatter);
    # 执行体填实值(分支 tip / tree / 分支名), 产品 ingest 据此校验后铸交回记录。占位值不算已填。
    # AIPOS-F93 件①: 键与逐键说明 = next_resolver.report_frontmatter_contract(与落点句 / my-tasks / 章程同源, 原本地 hints 表退役);
    # 声明缺 = SchemaLoadError 向上抛(fail-closed: 原「出声后出无 frontmatter 骨架」退役——无键模板会让执行体照抄出被拒报告)。
    from tools.aipos_cli.next_resolver import report_frontmatter_contract

    contract = report_frontmatter_contract("return", branch_task_id=task_id)
    frontmatter = _skeleton_frontmatter(contract)
    return frontmatter + f"""# RETURN — {task_id}

## 一句话结论
(待填写: 一句话概括任务完成情况)

## 改动清单
(待填写: 列出所有改动的文件及改动性质)

| 文件 | 改动性质 |
|------|----------|
| (待填写) | (待填写) |

## 验收对账
(待填写: 对照任务卡验收项, 逐项说明完成情况)

## 测试原文
(待填写: 粘贴测试运行的完整输出)

```
(待填写: 测试命令及输出)
```

## 排除物 + 理由
(待填写: 如有未完成项, 列出原因; 无则写"无排除物")
"""


def build_verdict_skeleton_markdown(audit_task_id: str, reviewed_task_id: str | None = None) -> str:
    """AIPOS-F89 件③c: 审计卡认领时的报告空模板(与真实报告同名同位: next_resolver.card_report_path)。

    frontmatter 键 = transitions artifact_ingest.verdict.required_frontmatter(verdict / commit_sha), 值为 `(待填写` 占位;
    报告完成判据(artifact_ingest.verdict.readiness = next_resolver.verdict_report_ready)只在这些键全部填实值后成立,
    故空模板「文件存在」不被 loop 等待 / artifact ingest / 开工核验当作完成。声明缺 = SchemaLoadError(fail-closed, 不出无键模板)。
    """
    from tools.aipos_cli.next_resolver import report_frontmatter_contract

    subject = reviewed_task_id or "<被审卡ID>"
    # AIPOS-F93 件①: 键与逐键说明 = report_frontmatter_contract(声明单源, 分支 = 被审卡分支)
    frontmatter = _skeleton_frontmatter(report_frontmatter_contract("verdict", branch_task_id=reviewed_task_id or None))
    return frontmatter + f"""# 审计报告 — {audit_task_id}(被审 {subject})

## 一句话结论
(待填写: 结论三值之一 + 一句话理由)

## Findings
(待填写: F-* 清单, 无则写「无」)

## 逐条验收核验
(待填写: 对照原卡验收断言逐条给出命令与输出摘录)
"""


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
