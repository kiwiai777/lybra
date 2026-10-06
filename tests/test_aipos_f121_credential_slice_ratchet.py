"""AIPOS-F121 件③ — 不变量夹具「测试源码不得出现打印凭据切片的写法」, 现存命中只减不增。

依据: tools/test_aipos_r1_scope.py 曾在 print 的 f-string 里对 executor token 取前 20 字符切片, 把真实工位凭据前缀打进 run-all 输出。
凭据只许指纹(confirm_client.token_fingerprint 等), 任何凭据/注册码的切片(前缀/后缀)都是泄漏——切片越长越可被重放或缩小
爆破面, 且落进 run-all 日志、审计报告、聊天转述。

扫描面(scoped_source_files): 产品仓文件清单(runall_discovery.repo_files: git 跟踪 + 未跟踪未忽略)中
  - run-all 自动发现的测试文件(workspace_config.discover_test_files + default_test_contract, 与门/执行器同一判定);
  - 任一 conftest.py;
  - 路径含 tests/ 目录段的 .py/.sh/.js/.ts 辅助脚本(夹具、手工活体脚本)。
式样(CREDENTIAL_SLICE_PATTERNS), 凭据名 = 标识符含 token/secret/passw/credential/bearer/api_key/code(大小写不敏感):
  - py_fstring_slice: f-string 插值花括号里对凭据名做下标切片(取前/后 N 个字符)
  - py_sink_slice:    同一行有输出口(print/log/_show/echo/write)且对凭据名做下标切片
  - sh_substring:     shell 对凭据名变量做子串展开(偏移:长度 形, 含负偏移)
  - js_slice:         对凭据名调用 slice / substring / substr
(各式样的命中/不误报样例见 test_patterns_catch_known_leak_shapes_and_spare_fingerprints, 注入串运行时拼接, 本文件源码不含命中形)
现存命中登记在 tests/f121_credential_slice_baseline.jsonl(读写口 tests/ratchet_baseline.py: 一行一条、稳定排序, 计数运行时算):
每条 = 文件 + 式样 id + 行指纹(行文本 strip 后 sha1 前 12 位) + 留存理由。判定(多重集, 键 = (file, pattern, fp)):
  新增命中 = 红; 基线条目已不存在(修好/删掉)= 红(须删该行, 只减不增)。
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

BASELINE_PATH = Path(__file__).resolve().parent / "f121_credential_slice_baseline.jsonl"

_NAME = r"[A-Za-z_]*(?:token|secret|passw|credential|bearer|api_?key|code)[A-Za-z_0-9]*"
_SLICE = r"\s*\[\s*-?\d*\s*:\s*-?\d*\s*\]"
CREDENTIAL_SLICE_PATTERNS: dict[str, re.Pattern[str]] = {
    "py_fstring_slice": re.compile(r"\{[^{}\n]*\b" + _NAME + _SLICE + r"[^{}\n]*\}", re.IGNORECASE),
    "py_sink_slice": re.compile(r"(?:\bprint\s*\(|\blog\w*\s*[.(]|\b_show\s*\(|\becho\b|\.write\s*\().*\b" + _NAME + _SLICE,
                                re.IGNORECASE),
    "sh_substring": re.compile(r"\$\{" + _NAME + r":(?:\s*\d|\s+-\d)", re.IGNORECASE),
    "js_slice": re.compile(r"\b" + _NAME + r"\.(?:slice|substring|substr)\s*\(", re.IGNORECASE),
}
_SCANNED_SUFFIXES = (".py", ".sh", ".js", ".ts")


def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def scoped_source_files(repo_root: Path = REPO_ROOT) -> list[str]:
    files = repo_files(repo_root)
    scope = set(discover_test_files(files, default_test_contract()))
    for rel in files:
        parts = rel.split("/")
        if parts[-1] == "conftest.py" or ("tests" in parts[:-1] and rel.endswith(_SCANNED_SUFFIXES)):
            scope.add(rel)
    return sorted(scope)


def scan_text(rel: str, text: str) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for line in text.splitlines():
        for pid, rx in CREDENTIAL_SLICE_PATTERNS.items():
            if rx.search(line):
                hits.append({"file": rel, "pattern": pid, "fp": fingerprint(line), "text": line.strip()[:160]})
                break  # 一行只记一次(首个命中式样)
    return hits


def scan_repo(repo_root: Path = REPO_ROOT) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    for rel in scoped_source_files(repo_root):
        # 解码失败 = 抛(fail-closed: 扫描面内的源码读不了不得当作「无命中」放过)
        hits.extend(scan_text(rel, (repo_root / rel).read_text(encoding="utf-8")))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str]:
    return (str(entry["file"]), str(entry["pattern"]), str(entry["fp"]))


def test_baseline_well_formed():
    entries = read_entries(BASELINE_PATH)
    for entry in entries:
        assert set(entry) == {"file", "pattern", "fp", "text", "reason"}, entry
        assert entry["pattern"] in CREDENTIAL_SLICE_PATTERNS, entry
        assert str(entry["reason"]).strip(), entry


def test_no_new_credential_slice_and_baseline_only_shrinks():
    hits = scan_repo()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new = current - allowed
    stale = allowed - current
    print(f"[③] 扫描面 {len(scoped_source_files())} 个测试源文件; 现存命中 {sum(current.values())} 处; 基线 {len(baseline)} 条")
    for hit in hits:
        print(f"  命中 {hit['file']} [{hit['pattern']}] {hit['text']}")
    assert not new, ("测试源码新增凭据切片写法(凭据只许指纹, 用 confirm_client.token_fingerprint): "
                     + "; ".join(f"{h['file']} [{h['pattern']}] {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修/已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_patterns_catch_known_leak_shapes_and_spare_fingerprints():
    """式样自检: 已知泄漏形必中, 指纹/普通切片不误报(注入串运行时拼接, 本文件源码自身不含命中形)。"""
    tok, code = "tok" + "en", "sc_" + "code"
    leaks = [
        f'print(f"✓ Auto-discovered executor token: {{{tok}[:20]}}...")',  # r1 原形
        f'    print(f"自包含码: {{{code}[:60]}}...\\n")',
        f'echo "Lybra token前8位: ${{LYBRA_{tok.upper()}:0:8}}..."',
        f"logger.info('x %s', {tok}[:8])",
        f"console.log({tok}.slice(0, 8))",
        f'echo "${{SECRET_KEY: -4}}"',
    ]
    for line in leaks:
        assert scan_text("x", line), line
    clean = [
        f'print(f"fingerprint: {{token_fingerprint({tok})}}")',
        'print(f"{lines[:5]}")',
        f'echo "${{LYBRA_{tok.upper()}:-}}"',
        'value = digest[:12]',
        f'assert {tok} != old_{tok}',
    ]
    for line in clean:
        assert not scan_text("x", line), line


def test_scope_covers_all_test_directories():
    scope = scoped_source_files()
    for rel in ("tools/test_aipos_r1_scope.py", "tools/test_aipos_r2_enroll.py", "test_f003_cli_return_block_atomicity.py",
                "tests/fake_harness.py", "tools/aipos_cli/tests/f23_live_acceptance.sh",
                "tools/mcp_server/tests/manual_test_aipos366.py"):
        assert rel in scope, rel
    assert "tests/test_aipos_f121_credential_slice_ratchet.py" in scope
