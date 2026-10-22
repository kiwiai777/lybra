"""AIPOS-F214 件⑥⑦ — 停摆守卫复活与新棘轮(F208 §5 E46 / §8 R8 / R11)。

- R8「唯一实现」指针有效: schema 中 single_implementation 值与描述里「唯一实现 / 唯一判定 / 唯一判据 / 唯一出口 <模块>.<函数>」所指函数须在
  该产品模块顶层存在, 且产品代码里有 ≥1 处引用(AST: 名字 / 属性 / from-import; 测试代码不算); 现存失效入基线
  tests/f214_single_impl_pointer_baseline.jsonl(一行一条带理由, 只减不增)。
- R11 CJK 守卫只扫字符串字面量: 原 web/board/tests/test_aipos288_cjk_source_guard.py 全有或全无、注释里的中文也算命中, 自 F109 起
  known-failure = 无守卫(该文件在 web/ 下, 本卡车道外, 不改; run-all 以 exclude 声明由本棘轮取代)。本棘轮扫同一范围
  (web/board 的 static/*.js、static/*.html、*.py; 同一豁免文件与 `i18n-exempt:` 行标记; Python 的 _I18N_TEMPLATES 字典与 docstring 不算),
  只认字符串字面量(Python tokenize 的字符串 / f-string 片段; JS 词法的引号 / 模板串; HTML 去掉注释后的文本与 <script> 内的 JS 字面量),
  现存命中入基线 tests/f214_cjk_literal_baseline.jsonl(只减不增)。
另: 两个棘轮各附「故意改一侧即红」反例。只读扫描, 不起进程、不写盘(反例用内存里的文本 / 声明副本)。
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import read_entries  # noqa: E402

R8_BASELINE = Path(__file__).resolve().parent / "f214_single_impl_pointer_baseline.jsonl"
R11_BASELINE = Path(__file__).resolve().parent / "f214_cjk_literal_baseline.jsonl"


def _show(text: str) -> None:
    print(text, flush=True)


# ===========================================================================
# R8 「唯一实现」指针有效
# ===========================================================================

#: 产品模块所在目录(相对仓根); 测试目录与 test_* 文件不算产品代码
PRODUCT_DIRS = ("tools/aipos_cli", "tools/mcp_server", "tools", "web/board")
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_MODPATH = r"((?:tools/|web/)?(?:aipos_cli/|mcp_server/|board/)?[a-z_][a-z0-9_]*)(?:\.py)?"
#: 描述里的指针: 「唯一实现 / 唯一判定 / 唯一判据 / 唯一出口」后紧跟 <模块>.<函数>(或 <模块>.py::<函数>)
DESC_POINTER = re.compile(r"(?:唯一实现|唯一判定|唯一判据|唯一出口)[^A-Za-z_/]{0,4}" + _MODPATH + r"(?:\.|::)(" + _IDENT + r")")
#: single_implementation 值里的指针: 任一 <模块>.<函数> / <模块>.py::<函数>
SI_POINTER = re.compile(_MODPATH + r"(?:\.|::)(" + _IDENT + r")")


def _is_product(path: Path) -> bool:
    return "tests" not in path.parts and not path.name.startswith("test_")


def product_modules(root: Path = REPO_ROOT) -> dict[str, Path]:
    """模块路径键(`tools/aipos_cli/x` 与裸名 `x`)→ 文件。裸名重名时先到先得(PRODUCT_DIRS 序)。"""
    out: dict[str, Path] = {}
    for base in PRODUCT_DIRS:
        for path in sorted((root / base).glob("*.py")):
            if not _is_product(path):
                continue
            out.setdefault(f"{base}/{path.stem}", path)
            out.setdefault(path.stem, path)
    return out


def _top_level_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def product_references(root: Path = REPO_ROOT) -> Counter:
    """产品代码里每个名字被引用的次数(AST: Name / Attribute.attr / from-import 别名; 定义本身不算)。"""
    refs: Counter = Counter()
    files = [p for p in list(root.glob("tools/**/*.py")) + list(root.glob("web/**/*.py")) if _is_product(p)]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name):
                refs[node.id] += 1
            elif isinstance(node, ast.Attribute):
                refs[node.attr] += 1
            elif isinstance(node, ast.ImportFrom):
                refs.update(alias.name for alias in node.names)
    return refs


def _walk_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str, str]]:
    """(json 路径, 键, 字符串值)。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if isinstance(value, str):
                yield here, str(key), value
            else:
                yield from _walk_strings(value, here)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            if isinstance(value, str):
                yield f"{path}[{i}]", "", value
            else:
                yield from _walk_strings(value, f"{path}[{i}]")


#: python 点分模块路径(`python3 -m tools.aipos_cli.x`)先归一成斜杠路径, 免把包名当「模块.函数」
_DOTTED_PACKAGE = re.compile(r"\b(tools|web)\.(aipos_cli|mcp_server|board)\.")


def schema_pointers(schemas: dict[str, Any]) -> set[tuple[str, str, str]]:
    """{(schema 文件名, 模块路径, 函数名)}。"""
    out: set[tuple[str, str, str]] = set()
    for file_name, data in schemas.items():
        for _path, key, raw in _walk_strings(data):
            text = _DOTTED_PACKAGE.sub(r"\1/\2/", raw)
            pattern = SI_POINTER if key == "single_implementation" else DESC_POINTER
            for match in pattern.finditer(text):
                out.add((file_name, match.group(1), match.group(2)))
    return out


def pointer_problems(schemas: dict[str, Any], root: Path = REPO_ROOT) -> list[dict[str, str]]:
    """失效指针 [{file, pointer, problem}]: no-module(带 tools/ 或 web/ 前缀却无此产品模块)/ no-def(模块顶层无此名)/
    no-caller(产品代码零引用)。裸模块名不是产品模块(如 schema 字段名 intent_face.harness)= 不是代码指针, 跳过。"""
    modules, refs = product_modules(root), product_references(root)
    problems: list[dict[str, str]] = []
    for file_name, mod, name in sorted(schema_pointers(schemas)):
        pointer = f"{mod}.{name}"
        path = modules.get(mod)
        if path is None:
            if mod.startswith(("tools/", "web/")) and name != "py":
                problems.append({"file": file_name, "pointer": pointer, "problem": "no-module"})
            continue
        if name == "py":
            continue  # `<模块>.py` 文件指针(无函数名)
        if name not in _top_level_names(path):
            problems.append({"file": file_name, "pointer": pointer, "problem": "no-def"})
        elif refs[name] == 0:
            problems.append({"file": file_name, "pointer": pointer, "problem": "no-caller"})
    return problems


def _load_schemas() -> dict[str, Any]:
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((REPO_ROOT / "schema").glob("*.schema.json"))}


def _r8_key(entry: dict) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["pointer"]), str(entry["problem"]))


def test_r8_baseline_well_formed():
    for entry in read_entries(R8_BASELINE):
        assert set(entry) == {"file", "pointer", "problem", "reason"} and str(entry["reason"]).strip(), entry
        assert entry["problem"] in ("no-module", "no-def", "no-caller"), entry


def test_r8_single_implementation_pointers_only_shrink():
    schemas = _load_schemas()
    problems = pointer_problems(schemas)
    pointers = schema_pointers(schemas)
    current, allowed = {_r8_key(p) for p in problems}, {_r8_key(e) for e in read_entries(R8_BASELINE)}
    new, stale = sorted(current - allowed), sorted(allowed - current)
    _show(f"[棘轮 R8] schema 指针 {len(pointers)} 个; 失效 {len(problems)} 个; 基线 {len(allowed)} 条")
    for p in problems:
        _show(f"  失效 [{p['problem']}] {p['file']}: {p['pointer']}")
    assert not new, f"schema「唯一实现」指针失效(函数不存在 / 产品零调用方; 改描述指向真实现, 或删死函数): {new}"
    assert not stale, f"基线条目已修 → 从 {R8_BASELINE.name} 删除这些行(只减不增): {stale}"


def test_r8_red_on_tampered_pointer():
    """故意改一侧即红: 声明里写一个不存在的函数 / 一个零调用方的函数, 都被抓出。"""
    schemas = json.loads(json.dumps(_load_schemas()))
    schemas["verbs.schema.json"]["verbs"]["lybra_kickoff"]["single_implementation"] = "tools/aipos_cli/subagent_kickoff.render_kickoff_nope"
    schemas["card.schema.json"]["fields"]["conclusion_outcome"]["description"] += " 唯一判定 roadmap._fix_card_never_called_probe"
    found = {(p["pointer"], p["problem"]) for p in pointer_problems(schemas)}
    _show(f"[R8 反例] {sorted(found & {('tools/aipos_cli/subagent_kickoff.render_kickoff_nope', 'no-def'), ('roadmap._fix_card_never_called_probe', 'no-def')})}")
    assert ("tools/aipos_cli/subagent_kickoff.render_kickoff_nope", "no-def") in found
    assert ("roadmap._fix_card_never_called_probe", "no-def") in found
    # 零调用方: 把指针指向真实存在却无产品引用的函数(R8 基线里已登记的那类)
    entry = next(e for e in read_entries(R8_BASELINE) if e["problem"] == "no-caller")
    mod, _, name = entry["pointer"].rpartition(".")
    schemas["roles.schema.json"]["description"] = f"唯一实现 {mod}.{name}"
    assert any(p["file"] == "roles.schema.json" and p["problem"] == "no-caller" for p in pointer_problems(schemas))


# ===========================================================================
# R11 CJK 守卫只扫字符串字面量
# ===========================================================================

CJK = re.compile(r"[一-鿿㐀-䶿]")
WEB_BOARD = REPO_ROOT / "web" / "board"
SCAN_PATTERNS = ("static/*.js", "static/*.html", "*.py")
#: 与原守卫同一豁免(i18n 字典 / 独立登录流程)
EXEMPT_FILES = frozenset({"static/i18n.js", "static/login.html", "static/auth-chrome.js"})
EXEMPT_MARKER = "i18n-exempt:"
I18N_TEMPLATE_NAME = "_I18N_TEMPLATES"


def py_string_literals(text: str) -> list[tuple[int, str]]:
    """Python 字符串字面量(tokenize: STRING / f-string 片段), 排除 docstring 与 _I18N_TEMPLATES 字典; 注释天然不在其中。"""
    tree = ast.parse(text)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                skip.add(first.lineno)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == I18N_TEMPLATE_NAME for t in node.targets):
            skip.update(range(node.value.lineno, (node.value.end_lineno or node.value.lineno) + 1))
    kinds = {tokenize.STRING} | {getattr(tokenize, n) for n in ("FSTRING_MIDDLE", "TSTRING_MIDDLE") if hasattr(tokenize, n)}
    return [(tok.start[0], tok.string) for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type in kinds and tok.start[0] not in skip]


_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


def js_string_literals(text: str, line_base: int = 0) -> list[tuple[int, str]]:
    """JS 字符串字面量(单 / 双引号、模板串); 跳过 // 与 /* */ 注释及正则字面量(按前一个有效字符判)。"""
    out: list[tuple[int, str]] = []
    i, n, line, prev = 0, len(text), 1 + line_base, ""
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += text.count("\n", i, j)
            i = j
            continue
        if c in "'\"`":
            j, start = i + 1, line
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif text[j] == "\n":
                    line += 1
                    if c != "`":
                        break
                j += 1
            out.append((start, text[i:j + 1]))
            i, prev = j + 1, c
            continue
        if c == "/" and prev in _REGEX_PREV:
            j, in_class = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    in_class = True
                elif text[j] == "]":
                    in_class = False
                elif text[j] == "/" and not in_class:
                    break
                j += 1
            i, prev = j + 1, "/"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


_HTML_BLOCK = re.compile(r"<!--.*?-->|<script\b[^>]*>(.*?)</script>|<style\b[^>]*>.*?</style>", re.S)


def html_literals(text: str) -> list[tuple[int, str]]:
    """HTML: 去掉 <!-- --> 注释与 <style> 后的文本行(界面文字 / 属性值)+ <script> 内的 JS 字符串字面量。"""
    out: list[tuple[int, str]] = []

    def plain(segment: str, base: int) -> None:
        for k, row in enumerate(segment.split("\n")):
            if row.strip():
                out.append((base + k + 1, row.strip()))

    last = 0
    for match in _HTML_BLOCK.finditer(text):
        plain(text[last:match.start()], text.count("\n", 0, last))
        if match.group(1) is not None:
            out.extend(js_string_literals(match.group(1), text.count("\n", 0, match.start(1))))
        last = match.end()
    plain(text[last:], text.count("\n", 0, last))
    return out


def literal_hits(rel: str, text: str) -> list[dict[str, str]]:
    suffix = rel.rsplit(".", 1)[-1]
    literals = (py_string_literals(text) if suffix == "py" else js_string_literals(text) if suffix == "js" else html_literals(text))
    lines = text.splitlines()
    hits = []
    for lineno, literal in literals:
        if not CJK.search(literal) or EXEMPT_MARKER in (lines[lineno - 1] if 0 < lineno <= len(lines) else ""):
            continue
        hits.append({"file": rel, "fp": hashlib.sha256(literal.encode("utf-8")).hexdigest()[:12], "text": literal.strip()[:80]})
    return hits


def scan_cjk_literals(root: Path = WEB_BOARD) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for pattern in SCAN_PATTERNS:
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel not in EXEMPT_FILES:
                hits.extend(literal_hits(rel, path.read_text(encoding="utf-8")))
    return hits


def _r11_key(entry: dict) -> tuple[str, str]:
    return (str(entry["file"]), str(entry["fp"]))


def test_r11_baseline_well_formed():
    for entry in read_entries(R11_BASELINE):
        assert set(entry) == {"file", "fp", "text", "reason"} and str(entry["reason"]).strip(), entry


def test_r11_cjk_string_literals_only_shrink():
    hits = scan_cjk_literals()
    baseline = read_entries(R11_BASELINE)
    current, allowed = Counter(_r11_key(h) for h in hits), Counter(_r11_key(e) for e in baseline)
    new, stale = sorted((current - allowed).elements()), sorted((allowed - current).elements())
    _show(f"[棘轮 R11] web/board 字符串字面量含 CJK {len(hits)} 处(注释 / docstring / i18n 字典不算); 基线 {len(baseline)} 条")
    for h in hits:
        _show(f"  命中 {h['file']} [{h['fp']}] {h['text']}")
    assert not new, ("界面代码新增中文字面量(走 i18n 字典 / _I18N_TEMPLATES; 确属非界面文字加行尾 `i18n-exempt: <理由>`): "
                     + "; ".join(f"{h['file']} {h['text']}" for h in hits if _r11_key(h) in new))
    assert not stale, f"基线条目已不存在(已修)→ 从 {R11_BASELINE.name} 删除这些行(只减不增): {stale}"


def test_r11_only_string_literals_count_not_comments():
    """故意改一侧即红: 注释 / docstring / i18n 字典里的中文不算, 字符串字面量里的中文算(含 JS 模板串与 HTML 文本)。"""
    py = ('"""模块说明: 中文 docstring 不算"""\n_I18N_TEMPLATES = {"zh": {"k": "字典里的中文不算"}}\n'
          '# 注释里的中文不算\nX = "界面按钮"  # 尾注释也不算\nY = "x"  # i18n-exempt: 夹具\n')
    js = '// 注释: 中文\n/* 块注释 中文 */\nconst a = "新建项目";\nconst b = `模板 ${x} 串`;\nconst r = /[一-龥]/;\n'
    html = '<!-- 注释 中文 -->\n<div>页面标题</div>\n<script>// 中文注释\nlet s = "脚本文字";</script>\n'
    got = {h["file"]: [x["text"] for x in literal_hits(h["file"], h["src"])] for h in (
        {"file": "probe.py", "src": py}, {"file": "probe.js", "src": js}, {"file": "probe.html", "src": html})}
    _show(f"[R11 反例] {got}")
    assert got["probe.py"] == ['"界面按钮"']
    assert got["probe.js"] == ['"新建项目"', "`模板 ${x} 串`"]
    assert got["probe.html"] == ["<div>页面标题</div>", '"脚本文字"']
