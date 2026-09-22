"""AIPOS-F79D 件①②: 治理仓提交门四检——单源校验模块(hook 与 governance-commit 共用)。

病根(2026-09-22 chris/lybra 实撞):
  件① `lybra governance-commit --dry-run` PASS 而正式提交 exit 1 —— dry-run 只查 status/清单/台账,
      B①–B④ 四检只在 pre-commit hook 的 bash 里执行, 预演与执行判据不对称。
  件② hook 定位治理工作区根用 os.walk 取仓内第一个含 governance/+5_tasks/ 的目录, 共享仓永远命中
      2_projects/lybra, chris 提交时 B②/B③ 前缀指错。

本模块 = 四检规则的唯一实现:
  B① 代码文件禁止            (声明: config.schema governance_worktree.path_constraints.code_files_blacklist)
  B② 治理文档须带声明 frontmatter (声明: governance_structure.file_declarations.governance_doc.required_frontmatter)
  B③ decision_log/ append-only  (声明: file_declarations.decision_log_entry.append_only + paths.decision_log_dir)
  B④ records/** 新增须携 record_type (声明: file_declarations.record_file.required_frontmatter + paths.tasks_root/records)
  B⑤ 治理仓 HEAD 必须是 main    (AIPOS-F29 大项C, 沿用)

工作区根按文件归属(件②): 每个 staged 文件自其所在目录向上找最近的同时含 <governance 首段>/ 与
<tasks_root 首段>/ 的祖先目录作为它的工作区根; 同一提交可跨工作区, 各按各的根算 B②/B③/B④ 前缀。
找不到根的文件按仓根前缀(现行兜底)并出 warning。禁 os.walk 首命中。

调用方:
  - tools/hooks/governance-pre-commit(bash 壳): 只取 staged 清单(`git diff --cached --name-status --no-renames -z`)
    并 `python3 -m tools.aipos_cli.governance_guardrails --staged-stdin` 调本模块;
  - tools/aipos_cli/governance_commit.py: dry-run 与正式提交前对同一「将提交清单」调 check_entries + format_report。
  两侧拒因文案来自同一 format_report, 逐字相同。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class GuardrailDeclarationError(RuntimeError):
    """config.schema 声明缺失/不可读 —— fail-closed, 调用方必须拒绝放行。"""


STATUS_ADDED = "A"
STATUS_MODIFIED = "M"
STATUS_DELETED = "D"

# git name-status 码归一: T(类型变更)/C(复制) 视为修改/新增; R 不会出现(读取层 --no-renames)
_STATUS_NORMALIZE = {"A": STATUS_ADDED, "C": STATUS_ADDED, "M": STATUS_MODIFIED, "T": STATUS_MODIFIED, "D": STATUS_DELETED}

MAIN_BRANCH = "main"

CHECK_B1 = "B①"
CHECK_B2 = "B②"
CHECK_B3 = "B③"
CHECK_B4 = "B④"
CHECK_B5 = "B⑤"


@dataclass(frozen=True)
class GuardrailDeclarations:
    schema_path: Path
    gov_docs_rel: str
    decision_log_rel: str
    tasks_root_rel: str
    records_rel: str
    gov_doc_required_fm: tuple[str, ...]
    record_required_fm: tuple[str, ...]
    code_blacklist_patterns: tuple[str, ...]
    code_extensions: tuple[str, ...]
    decision_log_append_only: bool

    @property
    def gov_segment(self) -> str:
        return self.gov_docs_rel.split("/")[0]

    @property
    def tasks_segment(self) -> str:
        return self.tasks_root_rel.split("/")[0]


@dataclass
class GuardrailReport:
    checked: int
    violations: list[dict[str, str]] = field(default_factory=list)
    ws_prefixes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    branch: str | None = None
    branch_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.branch_ok and not self.violations

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "violations": list(self.violations),
            "ws_prefixes": dict(self.ws_prefixes),
            "warnings": list(self.warnings),
            "branch": self.branch,
            "branch_ok": self.branch_ok,
            "rejected_files": sorted({v["file"] for v in self.violations}),
        }


# ---------------------------------------------------------------------------
# 声明读取(单一源: config.schema.json)
# ---------------------------------------------------------------------------

def default_schema_dir() -> Path:
    """与 hook 同序: LYBRA_SCHEMA_DIR → 运行代码所在仓的 schema/。"""
    env = os.environ.get("LYBRA_SCHEMA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    from tools.schema_loader import code_repo_schema_root

    return code_repo_schema_root() / "schema"


def resolve_schema_dir(repo_root: Path | None) -> Path:
    """governance-commit 侧: --workspace-root(产品仓)/schema 存在则用它, 否则同 hook 序。"""
    if repo_root is not None:
        candidate = Path(repo_root) / "schema"
        if (candidate / "config.schema.json").is_file():
            return candidate
    return default_schema_dir()


def _rel(paths: dict[str, Any], key: str, default: str) -> str:
    return str(((paths.get(key) or {}).get("path")) or default).strip("/")


def _pattern_extension(pattern: str) -> str | None:
    """`**/*.py` → `py`; 非扩展名形态的模式不作 B① 扩展名判据(返回 None)。"""
    tail = pattern.rsplit("/", 1)[-1]
    if tail.startswith("*.") and "*" not in tail[2:] and tail[2:]:
        return tail[2:].lower()
    return None


def load_guardrail_declarations(schema_dir: Path | None = None) -> GuardrailDeclarations:
    schema_dir = Path(schema_dir) if schema_dir is not None else default_schema_dir()
    schema_path = schema_dir / "config.schema.json"
    if not schema_path.is_file():
        raise GuardrailDeclarationError(
            f"config.schema not found at {schema_path} (single source unavailable; set LYBRA_SCHEMA_DIR)"
        )
    try:
        declared = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuardrailDeclarationError(f"config.schema unreadable at {schema_path}: {exc.__class__.__name__}: {exc}") from exc

    gs = declared.get("governance_structure") or {}
    paths = gs.get("paths") or {}
    file_decls = gs.get("file_declarations") or {}
    if not paths or not file_decls:
        raise GuardrailDeclarationError(
            f"config.schema governance_structure.paths / file_declarations missing at {schema_path}"
        )

    gov_docs = _rel(paths, "governance_docs", "governance")
    decision_log = _rel(paths, "decision_log_dir", "governance/decision_log")
    tasks_root = _rel(paths, "tasks_root", "5_tasks")
    records = _rel(paths, "records", "records")
    records_full = f"{tasks_root}/{records}".strip("/")

    gov_doc_fm = tuple(str(x) for x in ((file_decls.get("governance_doc") or {}).get("required_frontmatter") or []))
    record_fm = tuple(str(x) for x in ((file_decls.get("record_file") or {}).get("required_frontmatter") or []))
    decision_append_only = bool((file_decls.get("decision_log_entry") or {}).get("append_only", True))

    path_constraints = ((declared.get("governance_worktree") or {}).get("path_constraints") or {})
    blacklist = tuple(str(x) for x in (path_constraints.get("code_files_blacklist") or []))
    if not blacklist:
        raise GuardrailDeclarationError(
            f"config.schema governance_worktree.path_constraints.code_files_blacklist missing/empty at {schema_path}"
        )
    extensions = tuple(sorted({ext for ext in (_pattern_extension(p) for p in blacklist) if ext}))
    if not extensions:
        raise GuardrailDeclarationError(
            f"config.schema code_files_blacklist declares no `**/*.<ext>` patterns at {schema_path}"
        )

    return GuardrailDeclarations(
        schema_path=schema_path,
        gov_docs_rel=gov_docs,
        decision_log_rel=decision_log,
        tasks_root_rel=tasks_root,
        records_rel=records_full,
        gov_doc_required_fm=gov_doc_fm,
        record_required_fm=record_fm,
        code_blacklist_patterns=blacklist,
        code_extensions=extensions,
        decision_log_append_only=decision_append_only,
    )


# ---------------------------------------------------------------------------
# 件②: 工作区根按文件归属
# ---------------------------------------------------------------------------

def workspace_prefix_for(repo_root: Path, repo_rel_path: str, decls: GuardrailDeclarations,
                         _cache: dict[str, tuple[str, bool]] | None = None) -> tuple[str, bool]:
    """返回 (ws_prefix, found)。ws_prefix 形如 ``2_projects/chris/``, 仓根即工作区根时为 ``""``。

    自文件所在目录向上(含仓根)找最近的同时含 <gov 首段>/ 与 <tasks 首段>/ 的目录。
    """
    parts = [p for p in repo_rel_path.split("/") if p]
    for depth in range(len(parts) - 1, -1, -1):
        candidate_rel = "/".join(parts[:depth])
        if _cache is not None and candidate_rel in _cache:
            hit = _cache[candidate_rel]
            if hit[1]:
                return hit
            continue
        candidate_dir = repo_root / candidate_rel if candidate_rel else repo_root
        found = (candidate_dir / decls.gov_segment).is_dir() and (candidate_dir / decls.tasks_segment).is_dir()
        if _cache is not None:
            _cache[candidate_rel] = (f"{candidate_rel}/" if candidate_rel else "", found)
        if found:
            return (f"{candidate_rel}/" if candidate_rel else "", True)
    return ("", False)


# ---------------------------------------------------------------------------
# 四检
# ---------------------------------------------------------------------------

def _frontmatter_block(text: str) -> str | None:
    """与原 hook awk 同义: 第一对 ``---`` 行之间的内容; 无 ``---`` 行 → None。"""
    lines = text.split("\n")
    if not any(line == "---" for line in lines):
        return None
    inside = False
    collected: list[str] = []
    seen_open = False
    for line in lines:
        if line == "---":
            if not seen_open:
                seen_open = True
                inside = True
                continue
            if inside:
                break
        elif inside:
            collected.append(line)
    return "\n".join(collected)


def _has_field(text: str, field_name: str) -> bool:
    prefix = f"{field_name}:"
    return any(line.startswith(prefix) for line in text.split("\n"))


def normalize_entries(entries: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """(status, repo_rel_path) 归一: 状态码取首字母映射到 A/M/D; 未知码整体拒(fail-closed)。"""
    out: list[tuple[str, str]] = []
    for status, path in entries:
        code = str(status or "").strip()[:1].upper()
        if code not in _STATUS_NORMALIZE:
            raise ValueError(f"unknown git status code {status!r} for {path!r}")
        if not path:
            raise ValueError("empty path in staged entries")
        out.append((_STATUS_NORMALIZE[code], path))
    return out


def check_entries(
    repo_root: Path,
    entries: Iterable[tuple[str, str]],
    decls: GuardrailDeclarations,
    *,
    current_branch: str | None,
) -> GuardrailReport:
    """对「将提交清单」跑 B①–B⑤。entries = [(status, 仓根相对路径)], 内容检查读工作树文件(与原 hook 同义)。"""
    repo_root = Path(repo_root)
    normalized = normalize_entries(entries)
    report = GuardrailReport(checked=len(normalized))
    cache: dict[str, tuple[str, bool]] = {}

    def add(file: str, check: str, reason: str) -> None:
        report.violations.append({"file": file, "check": check, "reason": reason})

    for status, file in normalized:
        prefix, found = workspace_prefix_for(repo_root, file, decls, cache)
        report.ws_prefixes[file] = prefix
        if not found:
            report.warnings.append(
                f"WARNING: no governance workspace root found above {file} "
                f"(no ancestor with {decls.gov_segment}/ + {decls.tasks_segment}/); using repo-root prefix"
            )
        gov_docs_prefix = f"{prefix}{decls.gov_docs_rel}/"
        decision_log_prefix = f"{prefix}{decls.decision_log_rel}/"
        records_prefix = f"{prefix}{decls.records_rel}/"
        abs_path = repo_root / file
        on_disk = abs_path.is_file()

        # B①: 代码文件禁止(新增/修改且在盘上; 删除代码文件不拦, 与原 hook ACM 同义)
        if status in (STATUS_ADDED, STATUS_MODIFIED) and on_disk:
            ext = file.rsplit(".", 1)[-1].lower() if "." in file.rsplit("/", 1)[-1] else ""
            if ext and ext in decls.code_extensions:
                add(file, CHECK_B1, "Code file forbidden in governance repo (B①)")

        # B②: 治理文档 .md 须带声明 frontmatter
        if status in (STATUS_ADDED, STATUS_MODIFIED) and on_disk and file.startswith(gov_docs_prefix) and file.endswith(".md"):
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            fm = _frontmatter_block(text)
            required = "|".join(decls.gov_doc_required_fm)
            if fm is None:
                add(file, CHECK_B2, f"Missing frontmatter (B②, required: {required})")
            else:
                for field_name in decls.gov_doc_required_fm:
                    if not _has_field(fm, field_name):
                        add(
                            file,
                            CHECK_B2,
                            f"Missing '{field_name}' field in frontmatter (B②, declared in config.schema file_declarations.governance_doc)",
                        )

        # B③: decision_log/ append-only(只许新增禁修改)
        if decls.decision_log_append_only and status == STATUS_MODIFIED and file.startswith(decision_log_prefix):
            add(file, CHECK_B3, "decision_log/ is append-only, modifications forbidden (B③)")

        # B④: records/** 新增文件必须携机器出生标记
        if status == STATUS_ADDED and on_disk and file.startswith(records_prefix):
            text = abs_path.read_text(encoding="utf-8", errors="replace")
            for field_name in decls.record_required_fm:
                if not _has_field(text, field_name):
                    add(
                        file,
                        CHECK_B4,
                        f"New record lacks required field '{field_name}' (B④, declared in config.schema file_declarations.record_file)",
                    )

    # B⑤ (AIPOS-F29): 治理仓 HEAD 必须是 main
    report.branch = current_branch
    report.branch_ok = current_branch == MAIN_BRANCH
    return report


def git_current_branch(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_root), check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


def git_toplevel(path: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(path), check=True, capture_output=True, text=True, timeout=30,
    )
    return Path(result.stdout.strip())


def read_staged_entries_from_bytes(raw: bytes) -> list[tuple[str, str]]:
    """解析 ``git diff --cached --name-status --no-renames -z`` 输出: status\\0path\\0..."""
    tokens = [t for t in raw.split(b"\0")]
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2 != 0:
        raise ValueError(f"malformed name-status -z stream ({len(tokens)} tokens)")
    entries: list[tuple[str, str]] = []
    for i in range(0, len(tokens), 2):
        entries.append((tokens[i].decode("utf-8", errors="surrogateescape"), tokens[i + 1].decode("utf-8", errors="surrogateescape")))
    return entries


def read_staged_entries(repo_root: Path) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "diff", "--cached", "--name-status", "--no-renames", "-z"],
        cwd=str(repo_root), check=True, capture_output=True, timeout=30,
    )
    return read_staged_entries_from_bytes(result.stdout)


def manifest_entries(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """governance_commit.collect_commit_manifest 清单 → (status, repo_path)。untracked_selected = 新增。"""
    mapping = {"modified": STATUS_MODIFIED, "added": STATUS_ADDED, "deleted": STATUS_DELETED, "untracked_selected": STATUS_ADDED}
    out: list[tuple[str, str]] = []
    for item in manifest.get("files", []):
        status = mapping.get(str(item.get("status")))
        if status is None:
            raise ValueError(f"unknown manifest status {item.get('status')!r} for {item.get('repo_path')!r}")
        out.append((status, str(item["repo_path"])))
    return out


# ---------------------------------------------------------------------------
# 文案(hook 与 governance-commit 同源)
# ---------------------------------------------------------------------------

def format_report(report: GuardrailReport, decls: GuardrailDeclarations) -> str:
    lines: list[str] = []
    lines.append(f"🔍 AIPOS-R6M/F79D: governance guardrails (declarations: {decls.schema_path}; ws_prefix per file)")
    for file in sorted(report.ws_prefixes):
        lines.append(f"  ws_prefix: {file} → {report.ws_prefixes[file] or '<repo-root>/'}")
    for warning in report.warnings:
        lines.append(f"  {warning}")
    if not report.branch_ok:
        lines.extend([
            "",
            "❌ BLOCKED: Governance repo commit must be on 'main' branch (AIPOS-F29 大项C)",
            "",
            f"Current branch: {report.branch}",
            "",
            "治理仓永远停 main, 卡分支只开在产品仓。",
            "下一步: 切回 main 分支后重试提交",
            "  git checkout main",
        ])
    if report.violations:
        lines.extend([
            "",
            "❌ BLOCKED: Governance repo commit violates AIPOS-R6M + F29 file-level guardrails",
            "",
            "Violations:",
        ])
        for v in report.violations:
            lines.append(f"  - {v['file']}: {v['reason']}")
        lines.extend([
            "",
            "Guardrails (AIPOS-R6M 大项B + AIPOS-A1 声明驱动 + F29 大项C; 单源 tools/aipos_cli/governance_guardrails.py):",
            f"  ① Code files forbidden in governance repo (config.schema code_files_blacklist: {', '.join(decls.code_extensions)})",
            "  ② Governance docs require frontmatter with declared fields:",
            f"     Required fields (from config.schema file_declarations.governance_doc): {'|'.join(decls.gov_doc_required_fm)}",
            "     ---",
            "     status: active",
            "     ---",
            "  ③ decision_log/ is append-only (modifications forbidden)",
            f"  ④ New records require declared fields (from config.schema file_declarations.record_file): {'|'.join(decls.record_required_fm)}",
            "  ⑤ Governance repo must be on 'main' branch (F29)",
        ])
    if report.ok:
        lines.append("✅ All governance guardrails passed. Commit allowed.")
    return "\n".join(lines)


def run_guardrails(
    repo_root: Path,
    entries: Iterable[tuple[str, str]],
    *,
    schema_dir: Path | None = None,
    current_branch: str | None = None,
) -> tuple[GuardrailReport, GuardrailDeclarations, str]:
    """一次调用: 读声明 → 四检 → 文案。current_branch 缺省从 repo_root 读。"""
    decls = load_guardrail_declarations(schema_dir)
    branch = current_branch if current_branch is not None else git_current_branch(repo_root)
    report = check_entries(repo_root, entries, decls, current_branch=branch)
    return report, decls, format_report(report, decls)


# ---------------------------------------------------------------------------
# hook 入口: python3 -m tools.aipos_cli.governance_guardrails --repo-root <git root> --staged-stdin
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="tools.aipos_cli.governance_guardrails",
        description="AIPOS-F79D: 治理仓提交门四检(hook 调用面; 规则单源, 与 lybra governance-commit --dry-run 同判据)",
    )
    parser.add_argument("--repo-root", required=True, help="治理仓 git 根(git rev-parse --show-toplevel)")
    parser.add_argument("--schema-dir", default=None, help="config.schema 所在目录(缺省: LYBRA_SCHEMA_DIR / 运行代码仓 schema/)")
    parser.add_argument("--staged-stdin", action="store_true", help="从 stdin 读 `git diff --cached --name-status --no-renames -z` 输出")
    parser.add_argument("--json", action="store_true", help="附加输出 JSON 报告")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    try:
        if args.staged_stdin:
            entries = read_staged_entries_from_bytes(sys.stdin.buffer.read())
        else:
            entries = read_staged_entries(repo_root)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"❌ BLOCKED: cannot read staged entries: {exc}")
        return 1

    if not entries:
        print("✅ No files to check.")
        return 0

    try:
        report, decls, text = run_guardrails(
            repo_root, entries, schema_dir=Path(args.schema_dir) if args.schema_dir else None
        )
    except (GuardrailDeclarationError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"❌ BLOCKED: {exc}")
        return 1
    print(text)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
