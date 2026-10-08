"""AIPOS-F133 验收③ — 不变量夹具「卡遍历只走 task_loader.iter_queue_task_paths, 禁各命令自 glob 队列目录」, 现存命中只减不增。

依据(10-08 只读调研): next_resolver.scan_project 与 brief._get_queue_summary 各自 glob 队列目录, 无 lane 概念; lane 视图要求
四个查看命令共用同一卡遍历(task_loader.iter_queue_task_paths)与同一 lane 派生(machine_zone.card_lane_key)。

式样(AST, 逐模块): 名字 taint —— 赋值右侧含 `queue_root_for(...)` 调用、或含已 taint 名字(含 `/` 拼接)的目标名记为「队列路径」;
命中 = 对队列路径(或直接含 queue_root_for(...) 的表达式)调用 `.glob(` / `.rglob(` / `.iterdir(`, 或 `os.listdir(` / `os.scandir(`
/ `os.walk(` 以之为参数。唯一实现本体 tools/aipos_cli/task_loader.py 不在扫描面。
扫描面: 产品仓文件清单(runall_discovery.repo_files)中的 .py 产品源码, 排除 run-all 自动发现的测试文件、conftest.py、路径含
tests/ 目录段的文件(与 F132 棘轮同一扫描面口径)。
现存命中登记在 tests/f133_queue_glob_baseline.jsonl(读写口 tests/ratchet_baseline.py: 一行一条、稳定排序, 计数运行时算):
每条 = 文件 + 所在函数 + 行指纹(行文本 strip 后 sha1 前 12 位) + 行文本 + 留存理由。
判定(多重集, 键 = (file, func, fp)): 新增命中 = 红; 基线条目已不存在 = 红(须删该行, 只减不增)。
"""
from __future__ import annotations

import ast
import hashlib
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import read_entries  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "f133_queue_glob_baseline.jsonl"
SINGLE_SOURCE = "tools/aipos_cli/task_loader.py"
_LISTING_ATTRS = {"glob", "rglob", "iterdir"}
_OS_LISTING = {"listdir", "scandir", "walk"}
_ROOT_CALL = "queue_root_for"


def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def scoped_product_files(repo_root: Path = REPO_ROOT) -> list[str]:
    files = repo_files(repo_root)
    tests = set(discover_test_files(files, default_test_contract()))
    out = []
    for rel in files:
        parts = rel.split("/")
        if not rel.endswith(".py") or rel in tests or parts[-1] == "conftest.py" or "tests" in parts[:-1] or rel == SINGLE_SOURCE:
            continue
        out.append(rel)
    return sorted(out)


def _queue_derived(node: ast.AST, tainted: set[str]) -> bool:
    """队列路径表达式: queue_root_for(...) 调用 / 已 taint 名字 / 二者以 `/` 拼接 / Path(...) 包一层 / 由它们组成的 list/tuple
    (for 循环逐目录)。经函数调用取内容(读卡、列目录结果)不传播——那是文件或数据, 不是队列目录。"""
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name == _ROOT_CALL:
            return True
        if name in {"Path", "resolve", "expanduser"}:
            inner = list(node.args) + ([func.value] if isinstance(func, ast.Attribute) else [])
            return any(_queue_derived(arg, tainted) for arg in inner)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _queue_derived(node.left, tainted)
    if isinstance(node, (ast.List, ast.Tuple)):
        return any(_queue_derived(elt, tainted) for elt in node.elts)
    return False


def _targets(node: ast.AST) -> list[str]:
    names: list[str] = []
    if isinstance(node, ast.Assign):
        for tgt in node.targets:
            names += [n.id for n in ast.walk(tgt) if isinstance(n, ast.Name)]
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
        names.append(node.target.id)
    elif isinstance(node, (ast.For, ast.comprehension)):
        names += [n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)]
    return names


def _value(node: ast.AST) -> ast.AST | None:
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        return node.value
    if isinstance(node, (ast.For, ast.comprehension)):
        return node.iter
    return None


def _enclosing(tree: ast.Module) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else name
            if hasattr(child, "lineno"):
                out.setdefault(child.lineno, child_name)
            visit(child, child_name)

    visit(tree, "<module>")
    return out


def _scopes(tree: ast.Module) -> list[ast.AST]:
    """taint 作用域 = 每个最外层函数(含其嵌套函数/lambda, 闭包共享名字)+ 模块顶层语句; 名字 taint 不跨函数传播。"""
    scopes: list[ast.AST] = []
    top: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append(node)
        elif isinstance(node, ast.ClassDef):
            scopes += [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            top += [n for n in node.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        else:
            top.append(node)
    scopes.append(ast.Module(body=top, type_ignores=[]))
    return scopes


def _scope_hits(scope: ast.AST) -> list[ast.Call]:
    tainted: set[str] = set()
    changed = True
    while changed:  # 名字 taint 不动点(作用域内, 按名)
        changed = False
        for node in ast.walk(scope):
            value = _value(node)
            if value is None or not _queue_derived(value, tainted):
                continue
            for name in _targets(node):
                if name not in tainted:
                    tainted.add(name)
                    changed = True
    out: list[ast.Call] = []
    for node in ast.walk(scope):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _LISTING_ATTRS and _queue_derived(func.value, tainted):
            out.append(node)
        elif (isinstance(func, ast.Attribute) and func.attr in _OS_LISTING and isinstance(func.value, ast.Name)
              and func.value.id == "os" and any(_queue_derived(arg, tainted) for arg in node.args)):
            out.append(node)
    return out


def scan_source(rel: str, text: str) -> list[dict[str, str]]:
    tree = ast.parse(text)
    lines = text.splitlines()
    funcs = _enclosing(tree)
    hits: list[dict[str, str]] = []
    for scope in _scopes(tree):
        for node in _scope_hits(scope):
            line = lines[node.lineno - 1]
            hits.append({"file": rel, "func": funcs.get(node.lineno, "<module>"), "fp": fingerprint(line), "text": line.strip()[:160]})
    return hits


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for rel in scoped_product_files(repo_root):
        # 解码/语法失败 = 抛(fail-closed: 扫描面内的源码读不了不得当作「无命中」放过)
        hits.extend(scan_source(rel, (repo_root / rel).read_text(encoding="utf-8")))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["func"]), str(entry["fp"]))


def test_baseline_well_formed():
    for entry in read_entries(BASELINE_PATH):
        assert set(entry) == {"file", "func", "fp", "text", "reason"}, entry
        assert str(entry["reason"]).strip(), entry


def test_no_new_queue_self_glob_and_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new, stale = current - allowed, allowed - current
    print(f"[③] 扫描面 {len(scoped_product_files())} 个产品源文件; 现存自 glob 队列命中 {sum(current.values())} 处; 基线 {len(baseline)} 条")
    for hit in hits:
        print(f"  命中 {hit['file']}::{hit['func']} {hit['text']}")
    assert not new, ("产品源码新增自 glob 队列目录(卡遍历改走 task_loader.iter_queue_task_paths, lane 经 machine_zone.card_lane_key): "
                     + "; ".join(f"{h['file']}::{h['func']} {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修/已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_lane_views_use_single_traversal_and_lane_key():
    """件①②: 四命令经同一卡遍历/lane 派生/过滤函数; 唯一实现各只定义一次。"""
    mz = (REPO_ROOT / "tools/aipos_cli/machine_zone.py").read_text(encoding="utf-8")
    for fn in ("card_lane_key", "lane_of_card", "resolve_lane_filter", "filter_rows_by_lane", "group_rows_by_lane"):
        assert mz.count(f"def {fn}(") == 1, fn
    assert "resolve_card_repo(root, metadata" in mz  # lane 解析走 workspace_config.resolve_card_repo
    nr = (REPO_ROOT / "tools/aipos_cli/next_resolver.py").read_text(encoding="utf-8")
    br = (REPO_ROOT / "tools/aipos_cli/brief.py").read_text(encoding="utf-8")
    lr = (REPO_ROOT / "tools/aipos_cli/loop_run_record.py").read_text(encoding="utf-8")
    cli = (REPO_ROOT / "tools/aipos_cli/aipos_cli.py").read_text(encoding="utf-8")
    assert "iter_queue_task_paths(workspace_root, states=_SCAN_STATES)" in nr and "iter_queue_task_paths(Path(governance_root))" in br
    assert "find_task_card(Path(governance_root), task_id)" in lr
    for src in (nr, br, lr, cli):
        assert "filter_rows_by_lane(" in src and "lane_of_card(" in src
    tc = (REPO_ROOT / "tools/aipos_cli/task_complexity.py").read_text(encoding="utf-8")
    assert tc.count("def unmet_dependencies(") == 1 and tc.count("def dependencies_satisfied(") == 1
    assert "dependency_audit_status" not in tc and "dependency_executor_status" not in tc  # 卡面自报字段不再被判据读
    for rel in ("tools/aipos_cli/next_resolver.py", "tools/aipos_cli/queue_mutation.py"):
        assert "unmet_dependencies(" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel


def test_pattern_catches_known_shapes_and_spares_benign():
    bad = '''
def f(root):
    queue_dir = queue_root_for(root)
    for state in ("pending", "claimed"):
        status_path = queue_dir / state
        for p in sorted(status_path.glob("*.md")):
            pass
    return list((queue_root_for(root) / "pending").iterdir())
'''
    hits = scan_source("x.py", bad)
    assert sorted(h["text"] for h in hits) == ['for p in sorted(status_path.glob("*.md")):',
                                                'return list((queue_root_for(root) / "pending").iterdir())'], hits
    clean = '''
def g(root):
    for p in iter_queue_task_paths(root):
        pass
    records = sorted(Path(root).glob("*.md"))
    target = queue_root_for(root) / "completed" / "x.md"
    return target.exists()
'''
    assert scan_source("y.py", clean) == []
