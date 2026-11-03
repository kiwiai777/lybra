"""AIPOS-F289 件①: 明文凭据判据(声明 transitions artifact_ingest.credential_guard)的读取与命中判定——自 artifact_ingest 原样迁出的轻模块。

一句话: 「哪里是凭据值」只有这一处判定(_credential_matches); 产物入口检查(artifact_ingest.credential_scan)、展示打码(credential_redact:
历史检索 / 报告视图)与 loop 进度打码(harness_launch.redact_progress: 只遮值不遮键名)同读。迁出原因: redact_progress 所在 harness_launch 经
workspace_config 懒导入而在 finalize 合并后预载闭包内, 直接导入 artifact_ingest 会把整个 CLI 拖进闭包(44 → 142 个模块); 本模块只依赖
标准库 + schema 读取(next_resolver 在闭包内且不展开)。artifact_ingest 原名照旧可用(从本模块导入), 无第二份实现。
"""
from __future__ import annotations

import re
from typing import Any, Iterator

_CREDENTIAL_KEYS = ("category", "scan", "patterns", "exempt", "texts")
_CREDENTIAL_SCAN_KEYS = ("full_text_dirs", "slot_globs", "output_target", "outputs", "skip_gitignored", "skip_dirs", "sniff_bytes",
                         "max_scan_bytes", "max_listed")
_CREDENTIAL_EXEMPT_KEYS = ("strip_chars", "key_patterns", "value_patterns", "loose_value_patterns", "value_words", "value_substrings")
_CREDENTIAL_TEXTS = ("refusal", "hit", "more", "what_return", "what_verdict", "what_finalization", "stock_title", "stock_root",
                     "stock_clean", "stock_summary", "stock_slot", "stock_hit", "stock_next", "charter_rule")


_COMPILED_MEMO: list[Any] = []  # [原声明对象, 编译结果](只缓存最近一份; 键 = 对象身份)


def credential_guard_declaration() -> dict[str, Any] | None:
    """AIPOS-F239 件①: transitions artifact_ingest.credential_guard(唯一读取口)。缺本节 = None(不查, 行为不变); 键 / 文案不齐、
    正则编不过 = SchemaLoadError(fail-closed)。返回声明副本, 另带 _patterns [(kind, label, 编译后正则, 最短值长, loose)] 与 _exempt(编译后豁免)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration().get("credential_guard")
    if decl is None:
        return None
    if _COMPILED_MEMO and _COMPILED_MEMO[0] is decl:  # AIPOS-F289: loop 进度逐行打码同读, 同一份声明对象只编译一次(schema 缓存失效 = 新对象重编)
        return dict(_COMPILED_MEMO[1])
    where = "transitions.schema.json artifact_ingest.credential_guard"
    scan = decl.get("scan") if isinstance(decl, dict) else None
    exempt = decl.get("exempt") if isinstance(decl, dict) else None
    texts = decl.get("texts") if isinstance(decl, dict) else None
    if (not isinstance(decl, dict) or any(k not in decl for k in _CREDENTIAL_KEYS) or not str(decl["category"]).strip()
            or not isinstance(scan, dict) or any(k not in scan for k in _CREDENTIAL_SCAN_KEYS)
            or not isinstance(exempt, dict) or any(k not in exempt for k in _CREDENTIAL_EXEMPT_KEYS)
            or not isinstance(texts, dict) or not all(isinstance(texts.get(k), str) and texts[k].strip() for k in _CREDENTIAL_TEXTS)
            or not isinstance(decl["patterns"], list) or not decl["patterns"]
            or not all(isinstance(scan[k], list) for k in ("full_text_dirs", "slot_globs", "skip_dirs"))
            or not all(isinstance(scan[k], int) and not isinstance(scan[k], bool) and scan[k] > 0
                       for k in ("sniff_bytes", "max_scan_bytes", "max_listed"))
            or not all(isinstance(scan[k], bool) for k in ("output_target", "outputs", "skip_gitignored"))
            or not all(isinstance(exempt[k], list) for k in ("key_patterns", "value_patterns", "loose_value_patterns", "value_words",
                                                             "value_substrings"))
            or not isinstance(exempt["strip_chars"], str)):
        raise SchemaLoadError(f"{where}({list(_CREDENTIAL_KEYS)} / scan.{list(_CREDENTIAL_SCAN_KEYS)} / exempt.{list(_CREDENTIAL_EXEMPT_KEYS)} / "
                              f"texts.{list(_CREDENTIAL_TEXTS)})未声明齐(AIPOS-F239 件①)")
    compiled: list[tuple[str, str, re.Pattern[str], int, bool]] = []
    try:
        for item in decl["patterns"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("kind", "label", "regex")):
                raise SchemaLoadError(f"{where}.patterns 每项须有 kind / label / regex(AIPOS-F239 件①)")
            low = item.get("min_value_chars", 1)
            if not isinstance(low, int) or isinstance(low, bool) or low < 1:
                raise SchemaLoadError(f"{where}.patterns[{item['kind']}].min_value_chars 须为 ≥ 1 的整数")
            if not isinstance(item.get("loose", False), bool):
                raise SchemaLoadError(f"{where}.patterns[{item['kind']}].loose 须为布尔")
            compiled.append((item["kind"], item["label"], re.compile(item["regex"], re.MULTILINE), low, bool(item.get("loose", False))))
        exempt_patterns = [re.compile(str(p)) for p in exempt["value_patterns"]]
        key_patterns = [re.compile(str(p)) for p in exempt["key_patterns"]]
        loose_patterns = [re.compile(str(p)) for p in exempt["loose_value_patterns"]]
    except re.error as exc:
        raise SchemaLoadError(f"{where}: 正则编不过(fail-closed): {exc}") from exc
    out = {**decl, "_patterns": compiled, "_exempt": {"patterns": exempt_patterns, "keys": key_patterns, "loose": loose_patterns,
                                                      "words": {str(w).casefold() for w in exempt["value_words"]},
                                                      "substrings": [str(s).casefold() for s in exempt["value_substrings"]],
                                                      "strip": exempt["strip_chars"]}}
    _COMPILED_MEMO[:] = [decl, out]
    return dict(out)


def _credential_value_exempt(value: str, decl: dict[str, Any], min_chars: int, loose: bool = False) -> bool:
    """命中值是否豁免(声明 exempt 唯一判定): 去首尾引号 / 尾随标点后过短、全匹配占位式(loose 模式另加 loose_value_patterns)、等于占位词、
    含占位子串 = 豁免。"""
    ex = decl["_exempt"]
    core = value.strip().strip(ex["strip"])
    if len(core) < min_chars:
        return True
    folded = core.casefold()
    if folded in ex["words"] or any(s in folded for s in ex["substrings"]):
        return True
    return any(p.fullmatch(core) for p in ex["patterns"] + (ex["loose"] if loose else []))


def _credential_matches(text: str, decl: dict[str, Any]) -> Iterator[tuple[Any, tuple[int, int], str, str]]:
    """命中判定本体(credential_hits / credential_redact 同一判定): 逐条 (match, 命中值所在区间, kind, label)。"""
    for kind, label, rx, low, loose in decl["_patterns"]:
        for m in rx.finditer(text):
            has_value = "value" in rx.groupindex
            value = m.group("value") if has_value else m.group(0)
            key = m.group("key") if "key" in rx.groupindex else None
            if value is None or _credential_value_exempt(value, decl, low, loose):
                continue
            if key and any(p.fullmatch(key) for p in decl["_exempt"]["keys"]):
                continue
            yield m, (m.span("value") if has_value else m.span()), kind, label


def credential_hits(text: str, decl: dict[str, Any]) -> list[dict[str, Any]]:
    """AIPOS-F239 件①(唯一判定): 文本中的明文凭据命中 [{line, kind, label}](按行号、种类去重排序)。绝不返回命中值。
    AIPOS-F250: 同一处(命中值区间相交)被多条模式命中 = 只记最先声明的那条(专门写法声明在泛化模式之前), 计数按处。"""
    seen: dict[tuple[int, str], dict[str, Any]] = {}
    taken: list[tuple[int, int]] = []
    for m, span, kind, label in _credential_matches(text, decl):
        if any(span[0] < end and start < span[1] for start, end in taken):
            continue
        taken.append(span)
        line = text.count("\n", 0, m.start()) + 1
        seen.setdefault((line, kind), {"line": line, "kind": kind, "label": label})
    return [seen[k] for k in sorted(seen)]


def credential_redact(text: str, decl: dict[str, Any] | None, mask: str) -> str:
    """AIPOS-F247: 展示用打码——同一判定(_credential_matches)命中的值换成 mask(历史检索把审计报告里的命令等原文给 Owner 看时不露明文凭据)。
    decl = None(产品未声明判据)= 原样。只改展示文本, 不改任何文件。"""
    if not decl or not text:
        return text
    spans = sorted({span for _m, span, _k, _l in _credential_matches(text, decl)})
    out, cursor = [], 0
    for start, end in spans:
        if start < cursor:
            start = cursor
        if start >= end:
            continue
        out += [text[cursor:start], mask]
        cursor = end
    return "".join(out) + text[cursor:]


# ---------------------------------------------------------------------------
# AIPOS-F289 件①: loop 进度 / 运行记录 / loop status 打码的「无法定位值」例外(声明 credential_guard.progress_redaction)
# ---------------------------------------------------------------------------

_PROGRESS_KEYS = ("always_hide", "placeholder_values", "scheme_words", "key_list_shape", "benign_values", "terminators")


def progress_redaction_declaration() -> dict[str, Any] | None:
    """credential_guard.progress_redaction 唯一读取口(harness_launch.redact_progress 唯一调用)。credential_guard 未声明 / 本段缺 = None
    (调用方按旧口径整行隐去含凭据字样的行, fail-closed); 键不齐 / 正则编不过 = SchemaLoadError。返回 {always_hide / key_list_shape: 编译后正则,
    placeholders: [编译后正则], scheme_words / benign_values: casefold 集, terminators: 串}。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    guard = _artifact_ingest_declaration().get("credential_guard")
    raw = guard.get("progress_redaction") if isinstance(guard, dict) else None
    if raw is None:
        return None
    where = "transitions.schema.json artifact_ingest.credential_guard.progress_redaction"
    lists = ("placeholder_values", "scheme_words", "benign_values")
    if (not isinstance(raw, dict) or any(k not in raw for k in _PROGRESS_KEYS)
            or not all(isinstance(raw[k], str) and raw[k] for k in ("always_hide", "key_list_shape", "terminators"))
            or not all(isinstance(raw[k], list) and raw[k] and all(isinstance(v, str) and v.strip() for v in raw[k]) for k in lists)
            or any(c.isspace() for c in raw["terminators"])):
        raise SchemaLoadError(f"{where}({' / '.join(_PROGRESS_KEYS)})未声明齐或形不合(AIPOS-F289 件①)")
    try:
        always, shape = re.compile(raw["always_hide"]), re.compile(raw["key_list_shape"])
        placeholders = [re.compile(p) for p in raw["placeholder_values"]]
    except re.error as exc:
        raise SchemaLoadError(f"{where} 正则编不过(fail-closed): {exc}") from exc
    return {"always_hide": always, "key_list_shape": shape, "placeholders": placeholders,
            "scheme_words": {v.strip().casefold() for v in raw["scheme_words"]},
            "benign_values": {v.strip().casefold() for v in raw["benign_values"]}, "terminators": raw["terminators"]}
