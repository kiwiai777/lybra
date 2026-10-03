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


def _code_repo_for(workspace_root: Path, task_id: str, card_frontmatter: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    """AIPOS-F78C 件②③: 卡分支所在仓 = 该卡声明的仓(workspace_config.resolve_card_repo 唯一解析; 卡 lane.repo → project.json
    repos 清单 → code_repo 别名 → 治理根)。返回 (仓路径, 拒因 out 片段); 解析不到/不是 git 仓根 = (None, {category, reasons})。
    chris 实撞: 卡无 lane.repo、project.json 无 repos、code_repo 指治理根(非 git 仓)→ 这里停在 LANE_REPO_UNDECLARED(声明缺失, 非崩溃)。"""
    from tools.aipos_cli.workspace_config import CardRepoUnresolved, project_repos, resolve_card_repo

    try:
        repo = resolve_card_repo(workspace_root, {**card_frontmatter, "task_id": str(card_frontmatter.get("task_id") or task_id)})
    except CardRepoUnresolved as exc:
        return None, {"category": exc.code, "reasons": [exc.reason]}
    if not (repo / ".git").exists():
        lane = card_frontmatter.get("lane") if isinstance(card_frontmatter.get("lane"), dict) else {}
        lane_repo = str(lane.get("repo") or "").strip() or "未声明"
        try:
            declared = "已声明" if project_repos(workspace_root)["declared"] else "未声明"
        except CardRepoUnresolved as exc:
            return None, {"category": exc.code, "reasons": [exc.reason]}
        return None, {
            "category": "LANE_REPO_UNDECLARED",
            "reasons": [
                f"卡 {task_id} 解析到的产品仓 {repo} 不是 git 仓根(无 .git): 卡 lane.repo={lane_repo}, project.json repos 清单{declared}, "
                f"code_repo={project_repos(workspace_root)['code_repo'] or '未声明'}。"
                "出口: 在 project.json 声明 repos {default, items: {仓名: 绝对路径}} 清单, 并在卡 frontmatter lane.repo 写产物所在仓名(一卡一仓); "
                "单仓项目 `lybra project set-repo <name> --code-repo <真实产品仓>`"
            ],
        }
    return repo, {}


def _external_finalize_pending(workspace_root: Path, task_id: str) -> tuple[bool, dict[str, Any]]:
    """AIPOS-F78B 件②: 本卡是否处在「finalize_mode=external 且裁决 PASS 且尚无 finalization 记录」——即产物入口应读 FINALIZE Return。
    返回 (pending, records)。判据读声明: N5.guards.has_pass_verdict.allowed_verdict_values。"""
    from tools.aipos_cli.next_resolver import _finalize_mode, _read_task_records, _resolve_governance_path_with_relative, _transition_node

    if _finalize_mode(workspace_root) != "external":
        return False, {}
    records = _read_task_records(workspace_root, task_id)
    verdict = str((records.get("latest_verdict") or {}).get("verdict") or "").strip()
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    fin_dir = _resolve_governance_path_with_relative("records", workspace_root) / "finalizations" / task_id
    has_finalization = fin_dir.is_dir() and any(fin_dir.glob("finalization_*.md"))
    return (bool(verdict) and verdict in allowed and not has_finalization), records


def validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """校验一张卡的产物(执行卡=Return; R 卡=裁决报告; external finalize 且裁决 PASS 的执行卡=FINALIZE 卡 Return)是否可铸记录。纯校验, 不提交。

    返回 {ok, kind: return|verdict|finalization, category, reasons[], path, frontmatter, exit_code}。
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
        finalize_task_id_for,
        invalid_finalization_frontmatter,
        missing_return_frontmatter,
        return_artifact_dir,
    )
    from tools.aipos_cli.task_loader import AmbiguousTaskCard

    workspace_root = Path(workspace_root)
    is_audit = task_id.upper().endswith("R")
    kind = "verdict" if is_audit else "return"
    out: dict[str, Any] = {"ok": False, "kind": kind, "category": "", "reasons": [], "path": None, "frontmatter": {},
                           "exit_code": INGEST_EXIT_REJECTED}
    try:
        task_path, _queue = _find_task_in_queue(workspace_root, task_id)
    except AmbiguousTaskCard as exc:
        out["category"], out["reasons"] = "TASK_AMBIGUOUS", [str(exc)]
        return out
    if not task_path:
        out["category"], out["reasons"] = "TASK_NOT_FOUND", [f"queue 目录中找不到任务卡 {task_id}(按 frontmatter task_id 查找, 文件名不限)"]
        return out
    if not is_audit:
        pending, records = _external_finalize_pending(workspace_root, task_id)
        if pending:
            kind = out["kind"] = "finalization"
            out["records"] = records
    try:
        decl = _artifact_ingest_declaration()[kind]
    except (SchemaLoadError, KeyError) as exc:
        out["category"], out["reasons"] = "INGEST_DECLARATION_MISSING", [f"transitions.schema artifact_ingest.{kind}: {exc}"]
        return out
    card_fm = _read_frontmatter(task_path)

    if kind == "finalization":
        # AIPOS-F78B 件②: 外部 FINALIZE 卡 Return(落点 return_root/<finalize_task_id>, 与执行体 Return 同一候选序)
        fin_id = finalize_task_id_for(task_id, card_fm)
        out["finalize_task_id"] = fin_id
        path = find_return_artifact(workspace_root, fin_id)
        if path is None:
            cands = list(_artifact_ingest_declaration()["return"].get("return_file_candidates") or [])
            out["category"] = "INGEST_RETURN_MISSING"
            out["reasons"] = [
                f"未在声明落点 {return_artifact_dir(workspace_root, fin_id)} 找到 FINALIZE 卡 {fin_id} 的 Return(候选: {', '.join(map(str, cands))})。"
                f"出口: 外部 FINALIZE 卡执行体把 Return 落到该目录(project.json paths.return_root), frontmatter 含 "
                f"{', '.join(str(k) for k in decl.get('required_frontmatter') or [])}"
            ]
            return out
        out["path"] = str(path)
        fm = _read_frontmatter(path)
        out["frontmatter"] = fm
        problems = invalid_finalization_frontmatter(fm)
        if problems:
            out["category"] = "INGEST_FRONTMATTER_MISSING" if any("缺" in p for p in problems) else "INGEST_DEPLOY_STATUS_INVALID"
            out["reasons"] = [f"{path}: {p}(声明: transitions.schema artifact_ingest.finalization)" for p in problems]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", 0
        return out

    # AIPOS-F78C 件②: 核 tip/被审分支的仓 = 该卡(R 卡: 被审卡)声明的仓; 解析不到 = 声明缺失出口(非崩溃), 不进 git
    if is_audit:
        reviewed_for_repo = str(card_fm.get("reviewed_task_id") or task_id[:-1]).strip()
        try:
            reviewed_path, _rq = _find_task_in_queue(workspace_root, reviewed_for_repo)
        except AmbiguousTaskCard as exc:
            out["category"], out["reasons"] = "TASK_AMBIGUOUS", [str(exc)]
            return out
        repo_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
        code_repo, repo_problem = _code_repo_for(workspace_root, reviewed_for_repo, repo_fm)
    else:
        code_repo, repo_problem = _code_repo_for(workspace_root, task_id, card_fm)
    if code_repo is None:
        out["category"], out["reasons"] = repo_problem["category"], repo_problem["reasons"]
        return out

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
        # AIPOS-F78C 件③: Return 自述 repo(可选, 声明 artifact_ingest.return.optional_frontmatter)须与卡 lane.repo 解析仓一致
        optional = [str(k) for k in (decl.get("optional_frontmatter") or [])]
        self_repo = str(fm.get("repo") or "").strip() if "repo" in optional else ""
        if self_repo:
            from tools.aipos_cli.workspace_config import CardRepoUnresolved, resolve_card_repo

            try:
                self_repo_path = resolve_card_repo(workspace_root, {"task_id": task_id, "lane": {"repo": self_repo}})
            except CardRepoUnresolved as exc:
                self_repo_path = None
                self_repo_reason = exc.reason
            if self_repo_path is None or self_repo_path.resolve() != code_repo.resolve():
                out["category"] = "INGEST_REPO_MISMATCH"
                out["reasons"] = [
                    f"{path}: Return 自述 repo={self_repo!r} → {self_repo_path or '不可解析: ' + self_repo_reason} ≠ 卡 {task_id} lane.repo 解析仓 {code_repo}。"
                    "出口: 一卡一仓——执行体把产物提交到卡声明的仓, 或修正 Return frontmatter repo; 跨仓改动拆卡"
                ]
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
            f"未在声明落点找到已完成的审计报告(project.json paths.verdict_root / {task_id}; "
            f"候选: {', '.join(map(str, decl.get('verdict_file_candidates') or []))}; 完成判据 artifact_ingest.verdict.readiness: "
            f"{'/'.join(map(str, decl.get('required_frontmatter') or []))} 已填且非占位, 认领时的空模板不算)"
        ]
        return out
    out["path"] = str(path)
    fm = _read_frontmatter(path)
    out["frontmatter"] = fm
    # AIPOS-F89 件③c: 报告完成判据唯一实现 next_resolver.missing_verdict_frontmatter(占位 = 未填, 空模板不算产物)
    from tools.aipos_cli.next_resolver import missing_verdict_frontmatter

    missing = missing_verdict_frontmatter(fm)
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


# ---------------------------------------------------------------------------
# AIPOS-F90 件②: 报告模型字段由产品从会话记录填写(Owner 2026-10-02 裁定; 审计体自报连续五次不实)
# 定位器唯一声明 card.schema intent_face.harness.session_record_locators; 本节是唯一读取口
# ---------------------------------------------------------------------------

def _session_locators() -> dict[str, Any]:
    from tools.aipos_cli.machine_zone import intent_face_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (intent_face_declaration().get("harness") or {}).get("session_record_locators")
    if not isinstance(decl, dict) or not str(decl.get("missing_value") or "").strip():
        raise SchemaLoadError("card.schema.json intent_face.harness.session_record_locators(含 missing_value)未声明")
    return decl


def _dig(obj: Any, path: list[Any]) -> Any:
    for key in path or []:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _epoch(value: Any) -> float | None:
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _scan_session_records(locator: dict[str, Any], artifact: Path, since: float | None) -> tuple[list[str], list[str]]:
    """按定位器扫会话记录: 返回(写入该报告的条目所在模型列表, 会话记录文件名列表)。只读, 不改任何文件。"""
    import glob as _glob
    import json as _json

    target = artifact.resolve()
    needles = {str(target), str(artifact), _json.dumps(str(target))[1:-1]}
    write_names = {str(n) for n in locator.get("write_tool_names") or []}
    path_keys = [str(k) for k in locator.get("write_path_keys") or []]
    models: list[str] = []
    sources: list[str] = []
    for pattern in locator.get("session_globs") or []:
        for name in sorted(_glob.glob(os.path.expanduser(str(pattern)))):
            session = Path(name)
            try:
                if since is not None and session.stat().st_mtime < since:
                    continue
                text = session.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                import sys

                print(f"Warning: 会话记录不可读, 跳过 {session}: {exc}", file=sys.stderr)
                continue
            if not any(n in text for n in needles):
                continue
            session_cwd = ""
            for line in text.splitlines():
                if not any(n in line for n in needles) and session_cwd:
                    continue
                try:
                    entry = _json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if not session_cwd and entry.get("type") == "session":
                    session_cwd = str(entry.get("cwd") or "")
                content = _dig(entry, list(locator.get("content_path") or []))
                if not isinstance(content, list):
                    continue
                hit = False
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != locator.get("tool_call_type"):
                        continue
                    if str(item.get(str(locator.get("tool_name_key") or "name")) or "") not in write_names:
                        continue
                    tool_args = item.get(str(locator.get("tool_args_key") or "arguments"))
                    tool_args = tool_args if isinstance(tool_args, dict) else {}
                    for key in path_keys:
                        raw = str(tool_args.get(key) or "").strip()
                        if not raw:
                            continue
                        candidate = Path(raw).expanduser()
                        if not candidate.is_absolute():
                            base = str(entry.get("cwd") or session_cwd or "")
                            if not base:
                                continue
                            candidate = Path(base) / candidate
                        if candidate.resolve() == target:
                            hit = True
                model = str(_dig(entry, list(locator.get("model_path") or [])) or "").strip()
                if hit and model:
                    models.append(model)
                    sources.append(session.name)
    return models, sources


def _same_model(self_reported: str, located: str) -> bool:
    def norm(value: str) -> str:
        return value.strip().lower()

    reported = norm(self_reported)
    for model in located.split("+"):
        m = norm(model)
        if reported == m or reported.endswith("/" + m) or m.endswith("/" + reported):
            return True
    return False


def runtime_model_bundle(workspace_root: Path, *, writer_task_id: str, writer_fm: dict[str, Any], artifact_path: Path,
                         self_reported: str) -> dict[str, Any]:
    """AIPOS-F90 件②: 产品填写的模型字段(agent_runtime 形)。harness = 写报告那张卡的卡面 harness, 缺省 default_by_task_mode;
    定位器缺/会话记录里找不到写该报告的条目 = missing_value(「未声明会话记录」), 绝不采信自报; 自报只作对照, 不一致标 model_mismatch。"""
    from tools.aipos_cli.machine_zone import default_harness_for_task_mode
    from tools.aipos_cli.next_resolver import _read_task_records

    decl = _session_locators()
    missing = str(decl["missing_value"]).strip()
    harness = str(writer_fm.get("harness") or "").strip() or default_harness_for_task_mode(str(writer_fm.get("task_mode") or "code"))
    claimed_at = _epoch((_read_task_records(Path(workspace_root), writer_task_id).get("latest_claim") or {}).get("claimed_at"))
    locator = decl.get(harness)
    models: list[str] = []
    sources: list[str] = []
    if isinstance(locator, dict):
        models, sources = _scan_session_records(locator, Path(artifact_path), claimed_at - 60 if claimed_at else None)
    located = "+".join(sorted(set(models)))
    reported = str(self_reported or "").strip()
    bundle: dict[str, Any] = {
        "harness": harness,
        "model": located or missing,
        "model_source": f"{harness}:{','.join(sorted(set(sources)))}" if located else missing,
        "model_mismatch": bool(located and reported and not _same_model(reported, located)),
    }
    if reported:
        bundle["model_self_reported"] = reported
    return bundle


def ingest_task_artifact(
    task_id: str,
    workspace_root: Path,
    *,
    connection_json: str | None = None,
    dry_run: bool = False,
    execute: Any = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """产物入口主函数: 校验 → 推导核派生同一条薄壳命令 → 产品填模型字段 → execute_derived_action(既有执行体)提交。

    AIPOS-F90 件②: 推导核对 return/verdict 步派生的命令就是本入口(`lybra artifact ingest --kind`); 本入口执行的是推导核同时
    给出的 shell_command(唯一构建), 补上 runtime_model_bundle(运行时模型取自会话记录)后经 execute_derived_action 提交。
    裁决入门时审计报告全文由门快照进 records(transitions artifact_ingest.verdict.report_snapshot)。
    返回 {ok, exit_code, category, reasons, command, path, output, message, agent_runtime}。execute 可注入(靶场)。
    """
    from tools.aipos_cli.next_resolver import (
        _find_task_in_queue,
        _read_frontmatter,
        derive_next_step,
        execute_derived_action,
        with_runtime_model,
    )

    check = validate_task_artifact(task_id, workspace_root)
    result: dict[str, Any] = {
        "task_id": task_id, "kind": check["kind"], "ok": False, "exit_code": check["exit_code"],
        "category": check["category"], "reasons": list(check["reasons"]), "command": "", "path": check["path"],
        "output": "", "message": "", "agent_runtime": None,
    }
    if kind and kind != check["kind"]:
        result.update({"exit_code": INGEST_EXIT_REJECTED, "category": "INGEST_KIND_MISMATCH",
                       "reasons": [f"推导核声明入门节点 --kind {kind}, 但产物入口按卡与记录判定为 {check['kind']}: 卡态已变, 重推导后再入门"],
                       "message": "artifact ingest 拒: 入门节点不符"})
        return result
    if not check["ok"]:
        result["message"] = f"artifact ingest 拒: {check['category']}"
        return result

    derivation = derive_next_step(task_id, Path(workspace_root))
    action_type = derivation.get("verb") or ""
    expected_verb = {"return": "lybra_queue_return_dry_run", "verdict": "lybra_audit_verdict_dry_run", "finalization": "lybra_finalize"}[check["kind"]]
    if not derivation.get("derivable") or action_type != expected_verb:
        result["category"] = "INGEST_NOT_AT_RETURN_NODE"
        result["reasons"] = [
            f"推导核当前节点 {derivation.get('current_node')}/{derivation.get('current_state')} 的动作不是 {expected_verb}: "
            f"missing={derivation.get('missing_records')}; suggested={derivation.get('suggested_action')}"
        ]
        result["message"] = "artifact ingest 拒: 卡不在可交回/可裁决节点"
        return result
    shell_command = str(derivation.get("shell_command") or derivation.get("command") or "")
    if check["kind"] in ("return", "verdict"):
        writer_path, _q = _find_task_in_queue(Path(workspace_root), task_id)
        bundle = runtime_model_bundle(
            Path(workspace_root), writer_task_id=task_id, writer_fm=_read_frontmatter(writer_path) if writer_path else {},
            artifact_path=Path(str(check["path"])), self_reported=str((check.get("frontmatter") or {}).get("model") or ""),
        )
        result["agent_runtime"] = bundle
        shell_command = with_runtime_model(shell_command, kind=check["kind"], agent_runtime=bundle)
    result["command"] = shell_command
    if dry_run:
        result.update({"ok": True, "exit_code": 0, "category": "DRY_RUN", "message": "校验通过(未提交)"})
        return result
    if check["kind"] == "finalization":
        # AIPOS-F78B 件②: 同一 writer(finalization_record.write_finalization_record)铸记录; actor=被审卡 claim 记录实例,
        # authorization=裁决 verdict_id, commit/merge_commit/remote_ref/deploy_status 来自 FINALIZE Return frontmatter
        from tools.aipos_cli.finalization_record import write_finalization_record
        from tools.aipos_cli.next_resolver import _claimer_instance

        records = check.get("records") or {}
        fm = check["frontmatter"]
        claimer = _claimer_instance(records)
        verdict_id = str((records.get("latest_verdict") or {}).get("verdict_id") or "").strip()
        if not claimer or not verdict_id:
            result["category"] = "INGEST_RECORD_MISSING"
            result["reasons"] = [f"finalization 记录依据缺: claim 记录 agent_instance={claimer!r}, 裁决 verdict_id={verdict_id!r}"]
            result["message"] = "artifact ingest 拒: 记录依据不齐"
            result["exit_code"] = INGEST_EXIT_REJECTED
            return result
        try:
            rel = str(Path(check["path"]).resolve().relative_to(Path(workspace_root).resolve()))
        except ValueError:
            rel = str(check["path"])
        deploy_status = str(fm.get("deploy_status") or "").strip()
        merge_commit = str(fm.get("merge_commit") or "").strip()
        written = write_finalization_record(
            governance_root=Path(workspace_root), task_id=task_id, actor=claimer, commit=merge_commit, merge_commit=merge_commit,
            authorization_type="verdict_ref", authorization_ref=verdict_id, deployed=(deploy_status == "deployed"),
            deploy_status=deploy_status, remote_ref=str(fm.get("remote_ref") or "").strip(), finalize_return_ref=rel,
        )
        result["ok"] = bool(written.get("ok"))
        result["exit_code"] = 0 if result["ok"] else 1
        result["output"] = str(written.get("path") or "")
        result["message"] = "finalization 记录已落(external finalize, 来自 FINALIZE Return)" if result["ok"] else "finalization 记录写入失败"
        result["category"] = "OK" if result["ok"] else "RECORD_WRITE_FAILED"
        return result
    runner = execute or execute_derived_action
    exec_result = runner({**derivation, "command": shell_command}, Path(workspace_root), connection_json)
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
        kind=getattr(args, "kind", None),
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
