"""AIPOS-F87 件④: 防碎片化不变量「只减不增」棘轮夹具。

依据: governance/research/2026-10-02-fragmentation-audit.md(附录 grep 式样)。现存违规登记在基线清单
tests/f87_fragmentation_baseline.json(测试数据文件, 非 schema), 每条 = 文件 + 行指纹(行文本 sha1 前 12 位; 行号仅作定位参考,
不参与比对) + 复查报告条目号。

判定(每类不变量各自比对多重集):
  - 现有违规不在清单 = 新增违规 → 红
  - 清单条目在仓里已不存在(已修 / 行文本已改)= 清单残留 → 红(逼清单同步缩小; 修好后从清单删掉该条)

五类不变量:
  a) 产品代码(非测试/非文档)不出现写死的实例名/机器名/home 路径/治理根路径/信封 id
  b) 门 MCP 工具名均在 schema/verbs.schema.json `verbs` 声明(差集入基线)
  c) tools/mcp_server/tools.py 不得同名定义同一模块级函数两次(零容忍, 不入基线)
  d) 不得以路径子串判定治理仓或项目(`"ai-project-os" in` / `.includes("ai-project-os")` 等)
  e) 卡/记录 frontmatter 写入只许经单源 record_writer(render_markdown / render_frontmatter_block), 禁手拼 `---` 头
     (F87 件①后车道内零容忍; 清单内仅有车道外未能改的条目, 均标 lane_blocked, 见 F87 RETURN「产品缺口」)
  f) 产品 Python 代码不得出现宽捕获后只 pass 的静默吞错: `except Exception: pass` / `except BaseException: pass` / `except: pass`
     (含元组里带 Exception/BaseException; 处理体只有 pass/... 即算)。AIPOS-F115 件③加入: 车道内清零, 清单仅有车道外条目(lane_blocked)。
     判据走 AST(不靠行正则), 比对键 = (file, 所在函数, 指纹=except 行与处理体首行文本 sha1 前 12 位)。

产品文件集 = `git ls-files` 中 .py/.ts/.js/.mjs/.cjs/.sh 及带 shebang 的无后缀脚本, 去掉 tests/test/fixtures/__tests__/playwright
目录与 test_*/test-*/*.test.ts/*_test.py/conftest.py。
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "f87_fragmentation_baseline.json"
TOOLS_PY = "tools/mcp_server/tools.py"
VERBS_SCHEMA = "schema/verbs.schema.json"
# e) 唯一允许构造 frontmatter 文本的文件(单源本身)
FRONTMATTER_SINGLE_SOURCE = "tools/aipos_cli/record_writer.py"

PRODUCT_SUFFIXES = {".py", ".ts", ".js", ".mjs", ".cjs", ".sh"}
EXCLUDED_DIR_PARTS = {"tests", "test", "fixtures", "__tests__", "playwright"}

# a) 复查报告附录式样(卡面件④ a 原文)
HARDCODED_RE = re.compile(r"\.lybra\.kiwiai-dev|kiwiai-dev|/home/kiwi|~/projects/lybra|2_projects/lybra|pol_lybra_")
# d) 路径子串判定治理仓/项目
PATH_SUBSTRING_RE = re.compile(
    r"""["'](ai-project-os|2_projects)["']\s+(not\s+)?in\b|\.includes\(\s*["'](ai-project-os|2_projects)["']"""
)
# b) 门工具名(与复查报告 H8 同法: tools.py 中 "name": "lybra_…")
TOOL_NAME_RE = re.compile(r'"name":\s*"(lybra_[A-Za-z0-9_]+)"')
# e) TS/JS 中以 `---\n<key>:` 起头的字面量
TS_FRONTMATTER_RE = re.compile(r"""[`'"]---\\n[A-Za-z_][\w-]*:""")
FM_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*:")
# f) 宽捕获名(AIPOS-F115 件③)
BROAD_EXCEPT_NAMES = {"Exception", "BaseException"}


def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def product_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    files: list[str] = []
    for rel in out.split("\0"):
        if not rel:
            continue
        path = Path(rel)
        if any(part in EXCLUDED_DIR_PARTS for part in path.parts[:-1]):
            continue
        name = path.name
        if name.startswith(("test_", "test-")) or name.endswith((".test.ts", ".test.js", "_test.py")) or name == "conftest.py":
            continue
        full = REPO_ROOT / rel
        if not full.is_file():
            continue
        if path.suffix in PRODUCT_SUFFIXES:
            files.append(rel)
        elif path.suffix == "" and full.read_text(encoding="utf-8", errors="replace").startswith("#!"):
            files.append(rel)
    return sorted(files)


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")


def _line_hits(files: list[str], pattern: re.Pattern[str]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for rel in files:
        for lineno, line in enumerate(_read(rel).splitlines(), start=1):
            if pattern.search(line):
                hits.append({"file": rel, "line": lineno, "fp": fingerprint(line), "text": line.strip()[:160]})
    return hits


def scan_a_hardcoded(files: list[str]) -> list[dict[str, object]]:
    return _line_hits(files, HARDCODED_RE)


def scan_d_path_substring(files: list[str]) -> list[dict[str, object]]:
    return _line_hits(files, PATH_SUBSTRING_RE)


def scan_b_undeclared_tools() -> list[dict[str, object]]:
    declared = set(json.loads(_read(VERBS_SCHEMA))["verbs"])
    text = _read(TOOLS_PY)
    names = sorted(set(TOOL_NAME_RE.findall(text)))
    return [{"tool": name} for name in names if name not in declared]


def scan_c_duplicate_defs() -> list[dict[str, object]]:
    tree = ast.parse(_read(TOOLS_PY))
    counts = Counter(n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return [{"file": TOOLS_PY, "function": name, "count": count} for name, count in sorted(counts.items()) if count > 1]


def _literal_lead(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value)
    return None


def _py_frontmatter_construction_lines(text: str) -> list[int]:
    """手拼 frontmatter 的三种形: ①列表以 "---" 起头且(仅此一项 或 第二项是 `key:` 行) ②字面量以 `---\\n<key>:` 起头
    (含 f-string 以 `---\\n` 起头后接插值) ③ `"---\\n" + …` 拼接。正文分隔线(["---", "", …])与解析侧 startswith("---\\n") 不算。"""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "---":
            second = _literal_lead(node.elts[1]) if len(node.elts) > 1 else None
            if len(node.elts) == 1 or (second is not None and FM_KEY_RE.match(second)):
                lines.add(node.lineno)
        elif isinstance(node, (ast.Constant, ast.JoinedStr)):
            lead = _literal_lead(node)
            if lead is not None and lead.startswith("---\n") and (
                FM_KEY_RE.match(lead[4:]) or (isinstance(node, ast.JoinedStr) and lead == "---\n")
            ):
                lines.add(node.lineno)
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Add)
            and isinstance(node.left, ast.Constant)
            and node.left.value == "---\n"
        ):
            lines.add(node.lineno)
    return sorted(lines)


def scan_e_handrolled_frontmatter(files: list[str]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for rel in files:
        if rel == FRONTMATTER_SINGLE_SOURCE:
            continue
        text = _read(rel)
        src_lines = text.splitlines()
        if rel.endswith(".py"):
            linenos = _py_frontmatter_construction_lines(text)
        elif rel.endswith((".ts", ".js", ".mjs", ".cjs")):
            linenos = [i for i, line in enumerate(src_lines, start=1) if TS_FRONTMATTER_RE.search(line)]
        else:
            linenos = []
        for lineno in linenos:
            line = src_lines[lineno - 1]
            hits.append({"file": rel, "line": lineno, "fp": fingerprint(line), "text": line.strip()[:160]})
    return hits


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in BROAD_EXCEPT_NAMES for n in names)


def _is_pass_only(body: list[ast.stmt]) -> bool:
    return all(
        isinstance(stmt, ast.Pass)
        or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis)
        for stmt in body
    )


def _py_broad_except_pass(text: str) -> list[tuple[int, int, str]]:
    """f) 返回 [(except 行号, 处理体首行号, 所在函数限定名)]——宽捕获(裸 except / Exception / BaseException)且处理体只有 pass/...。"""
    hits: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, f"{scope}.{child.name}" if scope != "<module>" else child.name)
                continue
            if isinstance(child, ast.ExceptHandler) and _is_broad_handler(child) and _is_pass_only(child.body):
                hits.append((child.lineno, child.body[0].lineno, scope))
            visit(child, scope)

    visit(ast.parse(text), "<module>")
    return hits


def scan_f_broad_except_pass(files: list[str]) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        text = _read(rel)
        src_lines = text.splitlines()
        for except_line, body_line, scope in _py_broad_except_pass(text):
            joined = src_lines[except_line - 1].strip() + " | " + src_lines[body_line - 1].strip()
            hits.append({"file": rel, "line": except_line, "function": scope, "fp": fingerprint(joined), "text": joined[:160]})
    return hits


def _key(inv: str, item: dict[str, object]) -> tuple[str, ...]:
    if inv == "f":
        return (str(item["file"]), str(item["function"]), str(item["fp"]))
    if inv == "b":
        return (str(item["tool"]),)
    if inv == "c":
        return (str(item["file"]), str(item["function"]))
    return (str(item["file"]), str(item["fp"]))


def current_violations() -> dict[str, list[dict[str, object]]]:
    files = product_files()
    return {
        "a": scan_a_hardcoded(files),
        "b": scan_b_undeclared_tools(),
        "c": scan_c_duplicate_defs(),
        "d": scan_d_path_substring(files),
        "e": scan_e_handrolled_frontmatter(files),
        "f": scan_f_broad_except_pass(files),
    }


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def ratchet_diff(current: dict[str, list[dict[str, object]]], baseline: dict[str, object]) -> dict[str, dict[str, list]]:
    """返回 {inv: {"new": [...现有但不在清单], "stale": [...清单有但已不存在]}}(多重集比对)。"""
    out: dict[str, dict[str, list]] = {}
    invariants = baseline["invariants"]  # type: ignore[index]
    for inv, items in current.items():
        entries = invariants[inv]["entries"]  # type: ignore[index]
        cur = Counter(_key(inv, item) for item in items)
        base = Counter(_key(inv, entry) for entry in entries)
        new_keys = cur - base
        stale_keys = base - cur
        out[inv] = {
            "new": [item for item in items if new_keys.get(_key(inv, item), 0) > 0],
            "stale": [entry for entry in entries if stale_keys.get(_key(inv, entry), 0) > 0],
        }
    return out


# ------------------------------------------------------------------------------------------------
# 夹具
# ------------------------------------------------------------------------------------------------


def test_product_file_set_is_nonempty_and_excludes_tests():
    files = product_files()
    assert len(files) > 100, files[:5]
    assert FRONTMATTER_SINGLE_SOURCE in files and TOOLS_PY in files
    assert not [f for f in files if "/tests/" in f or Path(f).name.startswith("test_")]


def test_baseline_is_well_formed_and_counts_match():
    baseline = load_baseline()
    invariants = baseline["invariants"]
    assert set(invariants) == {"a", "b", "c", "d", "e", "f"}
    for inv, block in invariants.items():
        assert block["count"] == len(block["entries"]), (inv, block["count"], len(block["entries"]))
        for entry in block["entries"]:
            assert entry.get("item"), (inv, entry)  # 每条带复查报告条目号
            if inv in ("a", "d", "e", "f"):
                assert entry.get("file") and entry.get("fp") and isinstance(entry.get("line"), int), (inv, entry)
            if inv == "f":
                assert entry.get("function"), (inv, entry)
    assert baseline["total"] == sum(block["count"] for block in invariants.values())
    # c) 零容忍: 不入基线
    assert invariants["c"]["entries"] == []
    # e) 车道内零容忍: 清单只许有标注 lane_blocked 的车道外条目
    assert all(entry.get("lane_blocked") for entry in invariants["e"]["entries"]), invariants["e"]["entries"]
    # f) AIPOS-F115 件③: 车道内清零, 清单只许有标注 lane_blocked 的车道外条目
    assert all(entry.get("lane_blocked") for entry in invariants["f"]["entries"]), invariants["f"]["entries"]


def test_ratchet_no_new_violations_and_no_stale_baseline_entries():
    current = current_violations()
    baseline = load_baseline()
    diff = ratchet_diff(current, baseline)
    summary = {inv: len(items) for inv, items in current.items()}
    print(f"[F87 棘轮] 现存违规 每类: {summary} 合计 {sum(summary.values())}; 基线 total={baseline['total']}")
    problems: list[str] = []
    for inv, d in diff.items():
        for item in d["new"]:
            problems.append(f"[{inv}] 新增违规(不在基线): {json.dumps(item, ensure_ascii=False)}")
        for entry in d["stale"]:
            problems.append(
                f"[{inv}] 基线残留(仓内已不存在, 已修则须从 {BASELINE_PATH.name} 删除该条): {json.dumps(entry, ensure_ascii=False)}"
            )
    assert not problems, "\n".join(problems)


def test_c_tools_py_has_no_duplicate_module_level_defs():
    """c) 零容忍: lybra_gate_guidance 双定义已删(保留被 TOOL_HANDLERS 注册的 AIPOS-330 版), 描述符也只剩一份。"""
    assert scan_c_duplicate_defs() == []
    text = _read(TOOLS_PY)
    assert len(re.findall(r'"name":\s*"lybra_gate_guidance"', text)) == 1
    from tools.mcp_server.tools import TOOL_HANDLERS, lybra_gate_guidance

    assert TOOL_HANDLERS["lybra_gate_guidance"] is lybra_gate_guidance  # 模块级同名 = 被注册的那个
    assert "AIPOS-330 S3" in (lybra_gate_guidance.__doc__ or "")


def test_ratchet_detects_new_violation_and_stale_entry_red():
    """负夹具: 人造一条新增违规与一条已修条目, 比对必须双双报红(棘轮不是摆设)。"""
    current = current_violations()
    baseline = load_baseline()
    injected = {inv: list(items) for inv, items in current.items()}
    injected["a"].append({"file": "tools/aipos_cli/zz_new.py", "line": 1, "fp": fingerprint('X = "/home/kiwi/x"'), "text": "X"})
    injected["e"].append({"file": "tools/aipos_cli/zz_new.py", "line": 2, "fp": fingerprint('lines = ["---"]'), "text": "Y"})
    injected["f"].append({"file": "tools/aipos_cli/zz_new.py", "line": 3, "function": "probe",
                          "fp": fingerprint("except Exception: | pass"), "text": "Z"})
    if injected["d"]:
        injected["d"].pop()  # 模拟已修一条: 基线残留
    diff = ratchet_diff(injected, baseline)
    assert len(diff["a"]["new"]) == 1 and len(diff["e"]["new"]) == 1 and len(diff["f"]["new"]) == 1
    if current["d"]:
        assert len(diff["d"]["stale"]) == 1
    # 探测器本身: 手拼 frontmatter 三形 + 非 frontmatter 形不误报
    sample = (
        'a = ["---"]\n'
        'b = ["---", f"task_id: {x}"]\n'
        'c = "---\\nstatus: active\\n---\\n"\n'
        'd = "---\\n" + body\n'
        'e = ["---", "", "footer"]\n'
        'f = text.startswith("---\\n")\n'
        'g = f"---\\n{fm_text}\\n---\\n"\n'
    )
    assert _py_frontmatter_construction_lines(sample) == [1, 2, 3, 4, 7]
    # f) 探测器本身(AIPOS-F115 件③): 宽捕获 + 只 pass 才算; 精确捕获 / 有处理体不算
    f_sample = (
        "def a():\n    try:\n        x()\n    except Exception:\n        pass\n"            # 4 ✓
        "def b():\n    try:\n        x()\n    except:\n        pass  # c\n"               # 9 ✓
        "def c():\n    try:\n        x()\n    except (OSError, BaseException) as e:\n        ...\n"  # 14 ✓
        "def d():\n    try:\n        x()\n    except OSError:\n        pass\n"              # 精确捕获: 不算
        "def e():\n    try:\n        x()\n    except Exception as exc:\n        log(exc)\n"  # 有处理: 不算
        "class K:\n    def m(self):\n        try:\n            x()\n        except Exception: pass\n"  # 30 ✓
    )
    assert _py_broad_except_pass(f_sample) == [(4, 5, "a"), (9, 10, "b"), (14, 15, "c"), (30, 30, "K.m")]
