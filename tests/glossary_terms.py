"""AIPOS-F287 件①: 术语表(config.schema owner_texts.glossary)的唯一测试侧读取口与 avoid 命中判定——F277 英文块守卫、F262 第 4 步标题、
F287 仓根文档全文守卫三处同读(禁各写一套)。

读取经既有唯一 schema 加载器(tools.schema_loader.load_schema); 形不合 = AssertionError(fail-closed)。不写死任何术语: 术语、旧译、
例外短语、受守卫文档清单全在 glossary 一处声明。
判定: 不分大小写的子串匹配(与 F277 原判据同一口径); 每条 avoid_except 列出的短语先从行里抹掉(该短语里 avoid 词是另一个意思,
如 `board open` 的一次性登录票据), 再判 avoid 词。英文核 avoid, 中文核 avoid_zh(可选, 缺省 = 无)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

#: 语言 → 该语言旧译所在键(en 沿用 F277 起的 avoid; zh 为 AIPOS-F287 可选新键)
AVOID_KEY = {"en": "avoid", "zh": "avoid_zh"}


def glossary() -> dict[str, Any]:
    """config.schema owner_texts.glossary(形核: terms 非空, 每条 zh / en 非空, avoid* 为字符串列表, avoid_except 每项 {phrase, reason}
    且 phrase 含该条某个 avoid 词; guarded_docs 每种语言为非空路径列表)。"""
    from tools.schema_loader import code_repo_schema_root, load_schema

    decl = (load_schema("config", code_repo_schema_root()).get("owner_texts") or {}).get("glossary")
    assert isinstance(decl, dict), "config.schema owner_texts.glossary 未声明"
    terms = decl.get("terms")
    assert isinstance(terms, list) and terms, "glossary.terms 须为非空列表"
    for term in terms:
        assert isinstance(term, dict) and term.get("zh") and term.get("en"), f"glossary 条目须有 zh / en: {term!r}"
        for key in AVOID_KEY.values():
            words = term.get(key, [])
            assert isinstance(words, list) and all(isinstance(w, str) and w.strip() for w in words), f"{term['zh']}.{key} 形不合: {words!r}"
        for item in term.get("avoid_except", []):
            assert isinstance(item, dict) and item.get("phrase") and item.get("reason"), f"{term['zh']}.avoid_except 每项须 {{phrase, reason}}: {item!r}"
            words = [w.lower() for key in AVOID_KEY.values() for w in term.get(key, [])]
            assert any(w in item["phrase"].lower() for w in words), f"{term['zh']}.avoid_except {item['phrase']!r} 不含该条任何 avoid 词"
    docs = decl.get("guarded_docs")
    assert isinstance(docs, dict) and set(docs) >= set(AVOID_KEY), f"glossary.guarded_docs 须声明 {sorted(AVOID_KEY)}: {docs!r}"
    for lang in AVOID_KEY:
        assert isinstance(docs[lang], list) and docs[lang] and all(isinstance(p, str) and p for p in docs[lang]), f"guarded_docs.{lang}"
    return decl


def terms() -> list[dict[str, Any]]:
    return list(glossary()["terms"])


def term_by_zh(zh: str) -> dict[str, Any]:
    """按 zh 唯一取一条(取不到 / 不唯一 = 断言失败)。"""
    found = [t for t in terms() if t.get("zh") == zh]
    assert len(found) == 1 and found[0].get("en"), f"glossary 未唯一声明术语 {zh!r}: {found}"
    return found[0]


def guarded_docs(lang: str) -> list[str]:
    return list(glossary()["guarded_docs"][lang])


def avoid_words(term: dict[str, Any], lang: str) -> list[str]:
    return list(term.get(AVOID_KEY[lang], []))


def avoid_hits(text: str, term_list: list[dict[str, Any]], lang: str = "en") -> list[dict[str, Any]]:
    """文本逐行判 avoid 命中 → [{line, term, avoid, text}](行号从 1 起)。空 = 合格。"""
    hits: list[dict[str, Any]] = []
    for lineno, line in enumerate(str(text).splitlines(), start=1):
        low = line.lower()
        for term in term_list:
            words = avoid_words(term, lang)
            if not words:
                continue
            cleaned = low
            for item in term.get("avoid_except", []):
                cleaned = cleaned.replace(item["phrase"].lower(), " ")
            for bad in words:
                if bad.lower() in cleaned:
                    hits.append({"line": lineno, "term": term["zh"], "avoid": bad, "text": line.strip()[:160]})
    return hits


def doc_hits(repo_root: Path, term_list: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """guarded_docs 各文档全文命中 → [{file, lang, line, term, avoid, text}]。文档缺失 = 断言失败(不跳过)。"""
    term_list = terms() if term_list is None else term_list
    out: list[dict[str, Any]] = []
    for lang in AVOID_KEY:
        for rel in guarded_docs(lang):
            path = Path(repo_root) / rel
            assert path.is_file(), f"glossary.guarded_docs.{lang} 声明的文档不存在: {path}"
            for hit in avoid_hits(path.read_text(encoding="utf-8"), term_list, lang):
                out.append({"file": rel, "lang": lang, **hit})
    return out
