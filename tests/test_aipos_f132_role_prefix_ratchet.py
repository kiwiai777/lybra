"""AIPOS-F132 件③ — 不变量夹具「门侧授权判定不得按实例名前缀字面量判角色」, 现存命中只减不增。

依据(10-08 chris 首张 loop 试点): board_adapter 裁决提交按 `instance.split('.')[0] in {audit, auditor}`、返工节按前缀
`in {advisor, advise}` 判角色, 注册自定义角色(hbj-auditor → class auditor)的审计体 PASS 裁决被 ROLE_VIOLATION 拒。
角色判定唯一实现 = tools/aipos_cli/custom_roles.resolve_instance_role_class(实例 → 角色 → 角色类, 自定义角色读门注册表),
门侧授权判定唯一口 = custom_roles.authorize_instance_class。任何别处再按实例名首段/前缀字面量判角色 = 第二实现。

扫描面(scoped_product_files): 产品仓文件清单(runall_discovery.repo_files: git 跟踪 + 未跟踪未忽略)中的 .py 产品源码——
排除 run-all 自动发现的测试文件(workspace_config.discover_test_files + default_test_contract)、任一 conftest.py、
路径含 tests/ 目录段的文件。
式样(ROLE_PREFIX_PATTERNS):
  - first_segment:   对字符串取首个点分段(.split(".")[0] / .split(".", 1)[0] / .partition(".")[0]), 主机名(gethostname()/
                     含 host 的标识符)与模块名(__name__)取首段除外(那是主机段/包名推导, 不是角色)
  - prefix_startswith: .startswith("<词>.") 字面前缀判断
  - prefix_literal:  与含实例前缀字面量的集合做成员判断(in {...}/(...)/[...] 含 "<前缀>"; 相等比较不扫——"audit" 同时是
                     task_mode 值与 CLI 子命令名, == 形误报面过大), 前缀词 = roles.schema
                     roles[].naming.prefix 中不是角色名者(exec/audit…, 运行时读注册表)∪ 历史别名 advise
(各式样命中/不误报样例见 test_patterns_catch_known_shapes_and_spare_benign; 本文件在 tests/ 下不在扫描面)
现存命中登记在 tests/f132_role_prefix_baseline.jsonl(读写口 tests/ratchet_baseline.py: 一行一条、稳定排序, 计数运行时算):
每条 = 文件 + 式样 id + 行指纹(行文本 strip 后 sha1 前 12 位) + 留存理由(逐条说明为何不属门侧授权判定)。
判定(多重集, 键 = (file, pattern, fp)): 新增命中 = 红; 基线条目已不存在 = 红(须删该行, 只减不增)。
"""
from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import read_entries  # noqa: E402
from tools.aipos_cli.runall_discovery import repo_files  # noqa: E402
from tools.aipos_cli.workspace_config import default_test_contract, discover_test_files  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "f132_role_prefix_baseline.jsonl"
_LEGACY_PREFIX_ALIASES = ("advise",)  # 旧返工节判定曾放行的前缀别名(注册表无此前缀)


def _prefix_only_words() -> list[str]:
    """roles.schema naming.prefix 中不是角色名的前缀词(读注册表唯一加载器)+ 历史别名。"""
    from tools.schema_loader import load_schema

    roles = load_schema("roles").get("roles") or []
    names = {str(r.get("role") or "") for r in roles}
    prefixes = {str((r.get("naming") or {}).get("prefix") or "") for r in roles}
    words = sorted((prefixes - names - {""}) | set(_LEGACY_PREFIX_ALIASES))
    assert words, "roles.schema 无实例前缀声明(naming.prefix), 夹具无据"
    return words


def _patterns() -> dict[str, re.Pattern[str]]:
    words = "|".join(re.escape(w) for w in _prefix_only_words())
    return {
        "first_segment": re.compile(
            r"\.(?:split\(\s*['\"]\.['\"]\s*(?:,\s*1\s*)?\)|partition\(\s*['\"]\.['\"]\s*\))\s*\[\s*0\s*\]"),
        "prefix_startswith": re.compile(r"\.startswith\(\s*f?['\"][A-Za-z][\w-]*\.['\"]"),
        "prefix_literal": re.compile(
            r"\bin\s*[\{\(\[][^\n\}\)\]]*['\"](?:" + words + r")['\"]"),
    }


# first_segment 的非角色用途: 主机名取短名、模块名取包名
_FIRST_SEGMENT_BENIGN = re.compile(r"(?:gethostname\(\)|\b\w*host\w*|__name__)\s*\.(?:split|partition)\(")


def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def scoped_product_files(repo_root: Path = REPO_ROOT) -> list[str]:
    files = repo_files(repo_root)
    tests = set(discover_test_files(files, default_test_contract()))
    out = []
    for rel in files:
        parts = rel.split("/")
        if not rel.endswith(".py") or rel in tests or parts[-1] == "conftest.py" or "tests" in parts[:-1]:
            continue
        out.append(rel)
    return sorted(out)


def scan_text(rel: str, text: str, patterns: dict[str, re.Pattern[str]] | None = None) -> list[dict[str, str]]:
    pats = patterns or _patterns()
    hits: list[dict[str, str]] = []
    for line in text.splitlines():
        for pid, rx in pats.items():
            if not rx.search(line):
                continue
            if pid == "first_segment" and _FIRST_SEGMENT_BENIGN.search(line) and len(rx.findall(line)) == len(
                    _FIRST_SEGMENT_BENIGN.findall(line)):
                continue
            hits.append({"file": rel, "pattern": pid, "fp": fingerprint(line), "text": line.strip()[:160]})
            break  # 一行只记一次(首个命中式样)
    return hits


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    pats = _patterns()
    hits: list[dict[str, str]] = []
    for rel in scoped_product_files(repo_root):
        # 解码失败 = 抛(fail-closed: 扫描面内的源码读不了不得当作「无命中」放过)
        hits.extend(scan_text(rel, (repo_root / rel).read_text(encoding="utf-8"), pats))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["pattern"]), str(entry["fp"]))


def test_baseline_well_formed():
    entries = read_entries(BASELINE_PATH)
    for entry in entries:
        assert set(entry) == {"file", "pattern", "fp", "text", "reason"}, entry
        assert entry["pattern"] in _patterns(), entry
        assert str(entry["reason"]).strip(), entry


def test_no_new_role_prefix_judgement_and_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new = current - allowed
    stale = allowed - current
    print(f"[③] 扫描面 {len(scoped_product_files())} 个产品源文件; 前缀词 {_prefix_only_words()}; "
          f"现存命中 {sum(current.values())} 处; 基线 {len(baseline)} 条")
    for hit in hits:
        print(f"  命中 {hit['file']} [{hit['pattern']}] {hit['text']}")
    assert not new, ("产品源码新增按实例名前缀判角色的写法(门侧授权判定改走 custom_roles.authorize_instance_class / "
                     "resolve_instance_role_class): "
                     + "; ".join(f"{h['file']} [{h['pattern']}] {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修/已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_gate_authorization_sites_use_single_resolver():
    """件①②: 裁决提交 / 返工节两处门侧判定经 authorize_instance_class; 唯一实现各只定义一次。"""
    ba = (REPO_ROOT / "tools/aipos_cli/board_adapter.py").read_text(encoding="utf-8")
    assert ba.count("role_gate = authorize_instance_class(") == 2
    cr = (REPO_ROOT / "tools/aipos_cli/custom_roles.py").read_text(encoding="utf-8")
    assert cr.count("def resolve_instance_role_class(") == 1 and cr.count("def authorize_instance_class(") == 1
    dw = (REPO_ROOT / "tools/aipos_cli/draft_writer.py").read_text(encoding="utf-8")
    assert "resolve_instance_role_class(cand, repo_root)" in dw and "_registry_prefix_mapping" not in dw


def test_patterns_catch_known_shapes_and_spare_benign():
    """式样自检: 旧门判定形必中, 主机名/模块名取首段与普通比较不误报(本文件在 tests/ 下, 不在扫描面, 样例可直写)。"""
    bad = [
        'role_prefix = instance_text.split(".")[0].lower() if "." in instance_text else ""',
        'if role_prefix not in {"audit", "auditor"}:',
        'if role_prefix not in {"advisor", "advise"}:',
        'head = name.partition(".")[0]',
        'if instance.startswith("exec."):',
        'ok = head in ("exec", "executor")',
    ]
    for line in bad:
        assert scan_text("x", line), line
    clean = [
        'h = host or socket.gethostname().split(".")[0]',
        'instance_host = host.split(".")[0]',
        '_PRODUCT_PACKAGE = __name__.split(".", 1)[0]',
        'if role in ("executor", "auditor"):',
        'ext = file.rsplit(".", 1)[-1]',
        'if name.startswith("lybra_"):',
        'if str(fm.get("task_mode") or "").strip().lower() == "audit":',
    ]
    for line in clean:
        assert not scan_text("x", line), line
