"""AIPOS-196a gate-side scratch artifact ingestion.

Layer 2 confined-worker boundary: a confined worker can write only to a scratch
directory, never to Lybra truth. Proposed outputs become workspace-controlled
artifacts only when the gate ingests them during an Owner-confirmed queue_return.

This module is pure ingestion planning plus a guarded copy. It does not launch a
worker, mount anything, run a scheduler, or define a new controlled-execute
operation class. Ingestion only ever copies FROM an approved scratch root INTO
``workspace_artifacts/<task_id>/<return_id>/`` under the repo, and refuses any
symlink / relative-path escape or confused-deputy scratch root.
"""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from tools.schema_constants import RecordType
from pathlib import Path
from typing import Any




WORKSPACE_ARTIFACT_ROOT = Path("workspace_artifacts")
APPROVED_SCRATCH_ROOT_ENV = "LYBRA_APPROVED_SCRATCH_ROOT"
# Truth / control-plane prefixes that must never be a scratch source.
_TRUTH_PREFIXES = ("5_tasks", ".lybra")
_BLOCK_PREFIX = "ARTIFACT_INGEST_BLOCKED"
_MAX_INGEST_BYTES = 25 * 1024 * 1024


def has_scratch_request(scratch_dir: Any, scratch_artifact_refs: Any) -> bool:
    """True when the caller actually asked for scratch ingestion."""
    return bool(str(scratch_dir or "").strip()) or bool(_as_ref_list(scratch_artifact_refs))


def _as_ref_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def approved_scratch_root(env: dict[str, str] | None = None) -> Path | None:
    source = env if env is not None else os.environ
    raw = str(source.get(APPROVED_SCRATCH_ROOT_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _sanitize_component(name: str) -> str:
    cleaned = "".join(char if (char.isalnum() or char in "._-") else "-" for char in name).strip("-._")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "artifact"


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _within_truth(repo_root: Path, candidate: Path) -> bool:
    for prefix in _TRUTH_PREFIXES:
        if _is_within((repo_root / prefix).resolve(), candidate):
            return True
    return False


def plan_scratch_ingestion(
    *,
    repo_root: Path,
    task_id: str,
    return_id: str,
    scratch_dir: str | None,
    scratch_artifact_refs: Any,
    env: dict[str, str] | None = None,
    home_root: Path | None = None,
) -> dict[str, Any]:
    """Plan (and content-hash) a scratch->workspace ingestion without writing.

    Returns a dict with ``ingestions`` (each: scratch_resolved, scratch_ref,
    workspace_rel, content_sha256, size_bytes), ``workspace_refs``, a
    timestamp-independent ``digest`` (R-B snapshot integrity), and
    ``blocking_reasons``. Fails closed: any ambiguity is a blocking reason.

    AIPOS-227 — invariant: ``repo_root`` is ALREADY the resolved project truth root supplied by
    the single caller (board_adapter queue_return -> _resolve_repo_and_home). This function does
    not resolve; a future second caller feeding a raw/unresolved root is a contract violation and
    is fail-fast-guarded at the board boundary (R-2).

    ``home_root`` is the survivable truth home that contains all project subtrees, resolved by the
    home model. When supplied (production home path) a scratch source resolving anywhere inside
    ``<home>`` — i.e. into any project's truth, not just this project's — is refused. ``None``
    means the legacy/explicit/direct path (no home model in play), where the existing per-project
    truth guards already apply (R-1: ``None`` never stands in for an unresolved home).
    """
    refs = _as_ref_list(scratch_artifact_refs)
    blocking: list[str] = []
    result: dict[str, Any] = {
        "ingestions": [],
        "workspace_refs": [],
        "digest": "",
        "blocking_reasons": blocking,
    }
    if not has_scratch_request(scratch_dir, scratch_artifact_refs):
        return result

    repo_root = repo_root.resolve()

    # R-C: scratch_dir must live under an Owner/operator-approved root supplied
    # to the gate process out of band. The agent cannot widen this; without it
    # the gate refuses to act as a confused deputy.
    approved_root = approved_scratch_root(env)
    if approved_root is None:
        blocking.append(
            f"{_BLOCK_PREFIX}: no approved scratch root configured "
            f"({APPROVED_SCRATCH_ROOT_ENV} unset); refusing scratch ingestion"
        )
        return result

    scratch_raw = str(scratch_dir or "").strip()
    if not scratch_raw:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir is required when scratch_artifact_refs are provided")
        return result
    if not refs:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_refs is required when scratch_dir is provided")
        return result

    scratch_root = Path(scratch_raw).expanduser().resolve()
    if not _is_within(approved_root, scratch_root) and scratch_root != approved_root:
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir is outside the approved scratch root")
        return result
    if _within_truth(repo_root, scratch_root) or _is_within((repo_root / WORKSPACE_ARTIFACT_ROOT).resolve(), scratch_root):
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir resolves into a Lybra truth or workspace-artifact path")
        return result
    # AIPOS-227: extend-only — when the home model is in play, refuse a scratch source anywhere
    # inside the survivable truth home (any project's truth), not just this project's. Never
    # relaxes an existing guard; only widens the refused surface.
    if home_root is not None and _is_within(Path(home_root).expanduser().resolve(), scratch_root):
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir resolves into the Lybra truth home")
        return result
    if not scratch_root.exists() or not scratch_root.is_dir():
        blocking.append(f"{_BLOCK_PREFIX}: scratch_dir does not exist or is not a directory")
        return result

    dest_root = (repo_root / WORKSPACE_ARTIFACT_ROOT / _sanitize_component(task_id) / _sanitize_component(return_id)).resolve()
    workspace_artifact_root = (repo_root / WORKSPACE_ARTIFACT_ROOT).resolve()

    ingestions: list[dict[str, Any]] = []
    workspace_refs: list[str] = []
    seen_dest: set[str] = set()
    for ref in refs:
        if Path(ref).is_absolute():
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref must be relative to scratch_dir: {ref}")
            continue
        candidate = (scratch_root / ref).resolve()
        # Defeats symlink and ../ escape: the real path must stay in scratch.
        if not _is_within(scratch_root, candidate):
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref escapes scratch_dir: {ref}")
            continue
        if not candidate.exists() or not candidate.is_file():
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref is not a regular file: {ref}")
            continue
        size = candidate.stat().st_size
        if size > _MAX_INGEST_BYTES:
            blocking.append(f"{_BLOCK_PREFIX}: scratch_artifact_ref exceeds ingest size limit: {ref}")
            continue
        basename = _sanitize_component(Path(ref).name)
        dest_abs = (dest_root / basename).resolve()
        if not _is_within(workspace_artifact_root, dest_abs):
            blocking.append(f"{_BLOCK_PREFIX}: computed destination escapes workspace_artifacts: {ref}")
            continue
        dest_rel = dest_abs.relative_to(repo_root.resolve()).as_posix()  # AIPOS-240 r2: dest_abs is resolved (line above); symlink-safe render
        if dest_rel in seen_dest:
            blocking.append(f"{_BLOCK_PREFIX}: duplicate destination for ingested artifact: {dest_rel}")
            continue
        if dest_abs.exists():
            blocking.append(f"{_BLOCK_PREFIX}: workspace artifact already exists, refusing overwrite: {dest_rel}")
            continue
        seen_dest.add(dest_rel)
        content_sha256 = _hash_file(candidate)
        ingestions.append(
            {
                "scratch_resolved": str(candidate),
                "scratch_ref": ref,
                "workspace_rel": dest_rel,
                "content_sha256": content_sha256,
                "size_bytes": size,
            }
        )
        workspace_refs.append(dest_rel)

    if blocking:
        return result

    result["ingestions"] = ingestions
    result["workspace_refs"] = workspace_refs
    result["digest"] = ingestion_digest(ingestions)
    result["scratch_root"] = str(scratch_root)
    return result


def ingestion_digest(ingestions: list[dict[str, Any]]) -> str:
    """Timestamp-independent digest over ingested basenames + content hashes.

    Excludes return_id-bearing paths so the snapshot stays stable across the
    deterministic timestamp while still catching any content swap (R-B).
    """
    items = sorted(
        (Path(item["workspace_rel"]).name, str(item["content_sha256"]))
        for item in ingestions
    )
    encoded = "\n".join(f"{name}:{digest}" for name, digest in items)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def perform_scratch_ingestion(
    repo_root: Path,
    ingestions: list[dict[str, Any]],
    *,
    scratch_root: str | None = None,
    approved_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Copy planned scratch artifacts into workspace_artifacts (Owner-confirmed).

    R-A: each artifact is re-resolved and re-hashed at copy time and opened with
    O_NOFOLLOW, so a symlink or content swap between plan and copy is rejected
    rather than trusted. Raises ValueError on any integrity failure so the
    caller blocks the whole return instead of writing partial truth.
    """
    repo_root = repo_root.resolve()
    workspace_artifact_root = (repo_root / WORKSPACE_ARTIFACT_ROOT).resolve()
    scratch_root_resolved = Path(scratch_root).resolve() if scratch_root else None
    performed: list[dict[str, Any]] = []
    for item in ingestions:
        scratch_resolved = Path(str(item["scratch_resolved"])).resolve()
        if scratch_root_resolved is not None and not _is_within(scratch_root_resolved, scratch_resolved):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact left the scratch root before copy")
        if approved_root is not None and not _is_within(approved_root, scratch_resolved):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact left the approved root before copy")
        dest_abs = (repo_root / str(item["workspace_rel"])).resolve()
        if not _is_within(workspace_artifact_root, dest_abs):
            raise ValueError(f"{_BLOCK_PREFIX}: ingest destination escaped workspace_artifacts before copy")
        if dest_abs.exists():
            raise ValueError(f"{_BLOCK_PREFIX}: ingest destination appeared before copy: {item['workspace_rel']}")
        payload = _read_nofollow(scratch_resolved)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != str(item.get("content_sha256")):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact content changed before copy: {item['scratch_ref']}")
        dest_abs.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_abs, "xb") as handle:
            handle.write(payload)
        performed.append(
            {
                "path": str(item["workspace_rel"]),
                "kind": "create",
                "type": RecordType.INGESTED_ARTIFACT,
                "record_type": RecordType.INGESTED_ARTIFACT,
                "content_sha256": actual,
                "wrote": True,
            }
        )
    return performed


def _read_nofollow(path: Path) -> bytes:
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags | nofollow)
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            raise ValueError(f"{_BLOCK_PREFIX}: scratch artifact is not a regular file at copy time")
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
        return data
    finally:
        os.close(fd)


# ===========================================================================
# AIPOS-F78 件③: 产物入口(单一 ingest)—— `lybra artifact ingest --task-id <ID>`
#
# 执行引擎(pi/codex/claude-code)交回的是「分支 + Return 文件」; 产品按项目声明找文件、校验必填
# frontmatter 与分支 tip, 再经既有薄壳(queue return / audit-verdict, 驱动方 token)铸记录。
# 声明: transitions.schema.json artifact_ingest(文件候选/必填字段) + project.json paths(落点根)。
# 命令构建与执行复用 next_resolver(推导核派生同一条命令, execute_derived_action 同一执行体), 禁第二路径。
# 上面的 scratch ingestion(AIPOS-196a)是另一件事(confined worker 草稿区拷贝), 两者不互调。
# ===========================================================================

INGEST_EXIT_REJECTED = 4


def _git_out(repo: Path, *argv: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(["git", *argv], cwd=str(repo), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        import sys

        print(f"Warning: git {' '.join(argv)} failed in {repo}: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _code_repo_for(workspace_root: Path) -> Path:
    """卡分支所在仓: 治理根 project.json code_repo(唯一权威); 无声明且治理根自身是 git 仓 → 治理根(靶场单根)。"""
    from tools.aipos_cli.next_resolver import _resolve_code_repo_root

    return _resolve_code_repo_root(workspace_root) or workspace_root


def validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """校验一张卡的产物(执行卡=Return; R 卡=裁决报告)是否可铸记录。纯校验, 不提交。

    返回 {ok, kind: return|verdict, category, reasons[], path, frontmatter, exit_code}。
    fail-closed: 声明缺/文件缺/字段缺/sha 不符 = ok=False + 原文 + 出口。
    """
    from tools.schema_loader import SchemaLoadError
    from tools.aipos_cli.next_resolver import (
        _artifact_ingest_declaration,
        _check_verdict_artifact,
        _find_task_in_queue,
        _read_frontmatter,
        extract_return_summary_text,
        find_return_artifact,
        missing_return_frontmatter,
        return_artifact_dir,
    )

    workspace_root = Path(workspace_root)
    is_audit = task_id.upper().endswith("R")
    kind = "verdict" if is_audit else "return"
    out: dict[str, Any] = {"ok": False, "kind": kind, "category": "", "reasons": [], "path": None, "frontmatter": {},
                           "exit_code": INGEST_EXIT_REJECTED}
    try:
        decl = _artifact_ingest_declaration()[kind]
    except SchemaLoadError as exc:
        out["category"], out["reasons"] = "INGEST_DECLARATION_MISSING", [str(exc)]
        return out

    task_path, _queue = _find_task_in_queue(workspace_root, task_id)
    if not task_path:
        out["category"], out["reasons"] = "TASK_NOT_FOUND", [f"queue 目录中找不到任务卡 {task_id}"]
        return out
    code_repo = _code_repo_for(workspace_root)

    if not is_audit:
        path = find_return_artifact(workspace_root, task_id)
        if path is None:
            cands = list(decl.get("return_file_candidates") or [])
            out["category"] = "INGEST_RETURN_MISSING"
            out["reasons"] = [
                f"未在声明落点 {return_artifact_dir(workspace_root, task_id)} 找到 Return(候选: {', '.join(map(str, cands))})。"
                f"出口: 执行体把 Return 落到该目录(project.json paths.return_root 声明), 或修正声明"
            ]
            return out
        out["path"] = str(path)
        fm = _read_frontmatter(path)
        out["frontmatter"] = fm
        missing = missing_return_frontmatter(fm)
        if missing:
            out["category"] = "INGEST_FRONTMATTER_MISSING"
            out["reasons"] = [
                f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.return.required_frontmatter)。"
                f"出口: 执行体补齐后产品自动重试(loop 重推导)"
            ]
            return out
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out["category"], out["reasons"] = "INGEST_RETURN_UNREADABLE", [f"{path}: {exc}"]
            return out
        if not extract_return_summary_text(content):
            out["category"], out["reasons"] = "INGEST_SUMMARY_MISSING", [f"{path}: 「一句话结论」缺失或仍是骨架占位"]
            return out
        branch = str(fm.get("branch")).strip()
        commit_sha = str(fm.get("commit_sha")).strip()
        tree_hash = str(fm.get("tree_hash")).strip()
        tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
        if tip is None:
            out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 分支 {branch} 不存在(frontmatter.branch)"]
            return out
        if tip != commit_sha:
            out["category"] = "INGEST_TIP_MISMATCH"
            out["reasons"] = [
                f"分支 {branch} tip={tip[:12]} ≠ Return 自述 commit_sha={commit_sha[:12]}。"
                f"出口: 执行体以分支 tip 更新 Return frontmatter(交回的必须是当前产物)"
            ]
            return out
        tree = _git_out(code_repo, "rev-parse", f"{commit_sha}^{{tree}}")
        if tree != tree_hash:
            out["category"] = "INGEST_TREE_MISMATCH"
            out["reasons"] = [f"{commit_sha[:12]} tree={str(tree)[:12]} ≠ Return 自述 tree_hash={tree_hash[:12]}"]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", 0
        return out

    # R 卡: 裁决报告
    path = _check_verdict_artifact(workspace_root, task_id)
    if path is None:
        out["category"] = "INGEST_VERDICT_MISSING"
        out["reasons"] = [
            f"未在声明落点找到含 verdict 的审计报告(project.json paths.verdict_root / {task_id}; "
            f"候选: {', '.join(map(str, decl.get('verdict_file_candidates') or []))})"
        ]
        return out
    out["path"] = str(path)
    fm = _read_frontmatter(path)
    out["frontmatter"] = fm
    required = [str(k) for k in (decl.get("required_frontmatter") or [])]
    missing = [k for k in required if not str(fm.get(k) or "").strip()]
    if missing:
        out["category"] = "INGEST_FRONTMATTER_MISSING"
        out["reasons"] = [f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.verdict.required_frontmatter)"]
        return out
    reviewed = str(fm.get("reviewed_task_id") or task_id[:-1]).strip()
    from tools.schema_loader import get_branch_integration

    pattern = str(get_branch_integration().get("branch_pattern") or "card/{task_id}")
    branch = pattern.replace("{task_id}", reviewed)
    tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
    commit_sha = str(fm.get("commit_sha")).strip()
    if tip is None:
        out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 被审卡分支 {branch} 不存在"]
        return out
    if tip != commit_sha:
        out["category"] = "INGEST_TIP_MISMATCH"
        out["reasons"] = [f"被审分支 {branch} tip={tip[:12]} ≠ 报告自述 commit_sha={commit_sha[:12]}: 审的不是当前产物"]
        return out
    out["ok"], out["category"], out["exit_code"] = True, "OK", 0
    return out


def ingest_task_artifact(
    task_id: str,
    workspace_root: Path,
    *,
    connection_json: str | None = None,
    dry_run: bool = False,
    execute: Any = None,
) -> dict[str, Any]:
    """产物入口主函数: 校验 → 推导核派生同一条薄壳命令 → execute_derived_action(既有执行体)提交。

    返回 {ok, exit_code, category, reasons, command, path, output, message}。execute 可注入(靶场)。
    """
    from tools.aipos_cli.next_resolver import derive_next_step, execute_derived_action

    check = validate_task_artifact(task_id, workspace_root)
    result: dict[str, Any] = {
        "task_id": task_id, "kind": check["kind"], "ok": False, "exit_code": check["exit_code"],
        "category": check["category"], "reasons": list(check["reasons"]), "command": "", "path": check["path"],
        "output": "", "message": "",
    }
    if not check["ok"]:
        result["message"] = f"artifact ingest 拒: {check['category']}"
        return result

    derivation = derive_next_step(task_id, Path(workspace_root))
    action_type = derivation.get("verb") or ""
    expected_verb = "lybra_queue_return_dry_run" if check["kind"] == "return" else "lybra_audit_verdict_dry_run"
    if not derivation.get("derivable") or action_type != expected_verb:
        result["category"] = "INGEST_NOT_AT_RETURN_NODE"
        result["reasons"] = [
            f"推导核当前节点 {derivation.get('current_node')}/{derivation.get('current_state')} 的动作不是 {expected_verb}: "
            f"missing={derivation.get('missing_records')}; suggested={derivation.get('suggested_action')}"
        ]
        result["message"] = "artifact ingest 拒: 卡不在可交回/可裁决节点"
        return result
    result["command"] = str(derivation.get("command") or "")
    if dry_run:
        result.update({"ok": True, "exit_code": 0, "category": "DRY_RUN", "message": "校验通过(未提交)"})
        return result
    runner = execute or execute_derived_action
    exec_result = runner(derivation, Path(workspace_root), connection_json)
    result["ok"] = bool(exec_result.get("ok"))
    result["exit_code"] = 0 if result["ok"] else int(exec_result.get("exit_code") or 1)
    result["output"] = str(exec_result.get("output") or "")
    result["message"] = str(exec_result.get("message") or "")
    result["category"] = "OK" if result["ok"] else "SHELL_REJECTED"
    if not result["ok"] and result["output"]:
        result["reasons"] = [result["output"]]
    return result


def run_ingest_cli(args: Any) -> int:
    """CLI 入口(`lybra artifact ingest`): 治理根自发现或 --workspace-root; 输出人读或 --json; token 永不上屏。"""
    import json as _json
    import sys

    from tools.aipos_cli.aipos_cli import _find_repo_root_for_args

    try:
        governance_root = Path(getattr(args, "workspace_root", None) or _find_repo_root_for_args(args))
    except FileNotFoundError as exc:
        print(f"lybra artifact ingest: cannot resolve governance root: {exc}", file=sys.stderr)
        return INGEST_EXIT_REJECTED
    result = ingest_task_artifact(
        args.task_id, governance_root,
        connection_json=getattr(args, "connection_json", None), dry_run=bool(getattr(args, "dry_run", False)),
    )
    if getattr(args, "json", False):
        print(_json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "ok" if result["ok"] else f"rejected ({result['category']})"
        print(f"artifact ingest {args.task_id} [{result['kind']}]: {status}")
        if result.get("path"):
            print(f"  artifact: {result['path']}")
        if result.get("command"):
            print(f"  command: {result['command']}")
        for reason in result.get("reasons") or []:
            print(f"  - {reason}", file=sys.stderr if not result["ok"] else sys.stdout)
        if result.get("output") and result["ok"]:
            print(result["output"].rstrip())
    return int(result["exit_code"])


# AIPOS-316: Guard against direct invocation
from tools.aipos_cli._cli_entry_guard import check_direct_invocation
check_direct_invocation(__name__)
