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
import re
import stat as stat_module
from tools.schema_constants import RecordType
from tools.aipos_cli.verb_contract import declared_exit_code
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

# AIPOS-F101 件③: 退出码唯一声明 = verbs.schema verbs.lybra_artifact_ingest.exit_codes(原写死 4 与 transitions 重复声明删除);
# 模块名保留为读声明的值(既有调用方/夹具按名引用), 声明缺 = 导入即 SchemaLoadError(fail-closed)
INGEST_EXIT_RECORDED = declared_exit_code("lybra_artifact_ingest", "recorded")
INGEST_EXIT_SHELL_REJECTED = declared_exit_code("lybra_artifact_ingest", "shell_rejected")
INGEST_EXIT_REJECTED = declared_exit_code("lybra_artifact_ingest", "rejected")


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
    from tools.aipos_cli.next_resolver import _finalize_mode, _read_task_records, _transition_node

    if _finalize_mode(workspace_root) != "external":
        return False, {}
    records = _read_task_records(workspace_root, task_id)
    verdict = str((records.get("latest_verdict") or {}).get("verdict") or "").strip()
    allowed = [str(v) for v in (_transition_node("N5").get("guards", {}).get("has_pass_verdict", {}).get("allowed_verdict_values") or [])]
    from tools.aipos_cli.finalization_record import existing_finalization_records  # AIPOS-F120: 有无记录唯一判据

    has_finalization = bool(existing_finalization_records(workspace_root, task_id))
    return (bool(verdict) and verdict in allowed and not has_finalization), records


def slot_code_refusal(slot_dir: Path) -> dict[str, Any] | None:
    """AIPOS-F195 件①: 报告落点(含 artifacts/、run_long/ 等子目录)出现代码文件 = 拒入门(交回 / 审计报告 / 外部 FINALIZE 交回同此)。
    判据 = governance_guardrails.is_code_file(只读 config.schema code_files_blacklist), 拒因码与文案读 config.schema
    governance_worktree.path_constraints.artifact_slot_code_files(声明缺 = 不查, 行为不变; 声明坏 = 拒 INGEST_DECLARATION_MISSING)。
    返回 None(无代码文件)或 {category, reasons, code_files}。"""
    from tools.aipos_cli.governance_guardrails import GuardrailDeclarationError, load_guardrail_declarations, slot_code_files_in_dir

    try:
        decls = load_guardrail_declarations()
    except GuardrailDeclarationError as exc:
        return {"category": "INGEST_DECLARATION_MISSING", "reasons": [f"报告落点代码文件检查的声明不可用(fail-closed): {exc}"], "code_files": []}
    if decls.slot_code is None:
        return None
    found = slot_code_files_in_dir(Path(slot_dir), decls)
    if not found:
        return None
    files = "; ".join(f"{item['path']}(sha256 {item['sha256']}, {item['size']} 字节)" for item in found)
    category = decls.slot_code.ingest_category
    reason = decls.slot_code.text("ingest_reason", category=category, slot=str(slot_dir), count=len(found), files=files,
                                  extensions=", ".join(f".{e}" for e in decls.code_extensions))
    return {"category": category, "reasons": [reason], "code_files": found}


def validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
    """校验一张卡的产物(见 _validate_task_artifact)。AIPOS-F100 件②: 卡/记录/产物 frontmatter 读不出 = 拒
    INGEST_FRONTMATTER_UNREADABLE(原文点名文件与行号), 不取缺省继续校验。"""
    from tools.aipos_cli.frontmatter import FrontmatterReadError

    from tools.aipos_cli.audit_derivation import is_audit_card

    try:
        return _validate_task_artifact(task_id, workspace_root)
    except FrontmatterReadError as exc:
        return {"ok": False, "kind": "verdict" if is_audit_card(task_id) else "return",
                "category": "INGEST_FRONTMATTER_UNREADABLE", "reasons": [f"{exc}。出口: 按拒因修正该文件 frontmatter 后重推导"],
                "path": exc.path, "frontmatter": {}, "exit_code": INGEST_EXIT_REJECTED}


def _validate_task_artifact(task_id: str, workspace_root: Path) -> dict[str, Any]:
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
        verdict_artifact_dir,
    )
    from tools.aipos_cli.audit_derivation import audit_card_reviewed_id, is_audit_card
    from tools.aipos_cli.task_loader import AmbiguousTaskCard

    workspace_root = Path(workspace_root)
    is_audit = is_audit_card(task_id)
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
    # AIPOS-F112: 审计卡判据唯一实现 audit_derivation.is_audit_card(卡面为准, 认得复审轮 R2/R3…; 原按号尾 R 判)
    is_audit = is_audit_card(task_id, _read_frontmatter(task_path))
    kind = out["kind"] = "verdict" if is_audit else "return"
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
        refusal = slot_code_refusal(return_artifact_dir(workspace_root, fin_id))  # AIPOS-F195 件①
        if refusal is not None:
            out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
            return out
        fm = _read_frontmatter(path, allow_missing_block=True)
        out["frontmatter"] = fm
        problems = invalid_finalization_frontmatter(fm)
        if problems:
            out["category"] = "INGEST_FRONTMATTER_MISSING" if any("缺" in p for p in problems) else "INGEST_DEPLOY_STATUS_INVALID"
            out["reasons"] = [f"{path}: {p}(声明: transitions.schema artifact_ingest.finalization)" for p in problems]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
        return out

    # AIPOS-F197 件②: 只写治理根的卡(及其审计卡)交回契约分形(transitions artifact_ingest.<return|verdict>.governance_root):
    # 不核卡仓 / 卡分支(本卡无卡分支, 治理根不是产品仓), 改核产出清单(唯一实现 next_resolver.governance_outputs_manifest)
    from tools.aipos_cli.next_resolver import card_governance_outputs

    governance = card_governance_outputs(workspace_root, {**card_fm, "task_id": str(card_fm.get("task_id") or task_id)})
    # AIPOS-F78C 件②: 核 tip/被审分支的仓 = 该卡(R 卡: 被审卡)声明的仓; 解析不到 = 声明缺失出口(非崩溃), 不进 git
    repo_fm: dict[str, Any] = {}
    if is_audit:
        reviewed_for_repo = audit_card_reviewed_id(task_id, card_fm)
        try:
            reviewed_path, _rq = _find_task_in_queue(workspace_root, reviewed_for_repo)
        except AmbiguousTaskCard as exc:
            out["category"], out["reasons"] = "TASK_AMBIGUOUS", [str(exc)]
            return out
        repo_fm = _read_frontmatter(reviewed_path) if reviewed_path else {}
        code_repo, repo_problem = (workspace_root, {}) if governance else _code_repo_for(workspace_root, reviewed_for_repo, repo_fm)
    else:
        code_repo, repo_problem = (workspace_root, {}) if governance else _code_repo_for(workspace_root, task_id, card_fm)
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
        # AIPOS-F195 件①: 报告落点里的代码文件入门即拦(与产物形校验同处, 早于结案后 N6 提交门 B①)
        refusal = slot_code_refusal(return_artifact_dir(workspace_root, task_id))
        if refusal is not None:
            out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
            return out
        fm = _read_frontmatter(path, allow_missing_block=True)
        out["frontmatter"] = fm
        missing = missing_return_frontmatter(fm, governance=governance)
        if missing:
            where = "return.governance_root.required_frontmatter(只写治理根卡: 产出清单, 无分支三键)" if governance else "return.required_frontmatter"
            # AIPOS-F204 件①: 补法 = 本卡开工提示同一契约(候选主体卡 = 产品给值; 误用另一契约另附说明), 唯一实现 return_header_problem
            problem = out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, missing=missing)
            out["category"] = "INGEST_FRONTMATTER_MISSING"
            out["reasons"] = [
                f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.{where})。"
                f"出口: 执行体补齐后产品自动重试(loop 重推导)。补法: {problem['fix']}" + (f"。{problem['note']}" if problem["note"] else "")
            ]
            return out
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            out["category"], out["reasons"] = "INGEST_RETURN_UNREADABLE", [f"{path}: {exc}"]
            return out
        # AIPOS-F148 件②: 摘要来源读声明(transitions artifact_ingest.return.summary_source + 项目 return_summary_source,
        # 唯一读取口 workspace_config.return_summary_source), 与推导核就绪判据同一解析 extract_return_summary_text; 拒因同一文案
        from tools.aipos_cli.next_resolver import return_summary_missing_text
        from tools.aipos_cli.workspace_config import ReturnSummarySourceError, return_summary_source

        try:
            summary_source = return_summary_source(workspace_root)
        except (ReturnSummarySourceError, OSError, ValueError) as exc:
            out["category"], out["reasons"] = "INGEST_SUMMARY_MISSING", [
                f"{path}: 本项目 Return 摘要来源声明不可用(fail-closed): {exc}。出口: lybra project set-return-summary 重新声明"]
            return out
        if not extract_return_summary_text(content, summary_source):
            out["category"], out["reasons"] = "INGEST_SUMMARY_MISSING", [return_summary_missing_text(path, summary_source)]
            return out
        if governance:
            # AIPOS-F197 件②: 产出清单逐个核对并计算 sha256(唯一实现 governance_outputs_manifest; 门交回记录随后绑定同一清单)
            from tools.aipos_cli.next_resolver import GovernanceOutputsError, governance_outputs_manifest

            try:
                manifest = governance_outputs_manifest(workspace_root, task_id, card_fm, fm)
            except GovernanceOutputsError as exc:
                out["category"], out["reasons"] = exc.code, [
                    f"{path}: {exc.reason}(声明: transitions.schema artifact_ingest.return.governance_root)。出口: 执行体修正产出或 "
                    f"Return frontmatter outputs 后产品自动重试(loop 重推导)"]
                return out
            out["outputs_manifest"] = manifest
            out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
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
        # AIPOS-F196 件①: 交付主体为固定候选的卡(validation)= 核候选三键(candidate_subject_values 唯一实值), 不核卡分支 tip
        candidate_check = _candidate_subject_check(workspace_root, task_id, card_fm, fm, path, kind="return")
        if candidate_check is not None:
            mismatched = candidate_check.pop("mismatched", None)
            if mismatched:  # AIPOS-F204 件②: 三键 ≠ 产品给值 = 报告头可纠正(判定与补法唯一实现 return_header_problem)
                out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, mismatched=mismatched)
            out.update(candidate_check)
            return out
        branch = str(fm.get("branch")).strip()
        commit_sha = str(fm.get("commit_sha")).strip()
        tree_hash = str(fm.get("tree_hash")).strip()
        tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
        if tip is None:
            out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 分支 {branch} 不存在(frontmatter.branch)"]
            return out
        if tip != commit_sha:
            # AIPOS-F180B item 1: full values on both sides + form diagnosis (single implementation tip_mismatch_diagnosis)
            diag = out["tip_diagnosis"] = tip_mismatch_diagnosis(code_repo, tip, commit_sha, branch=branch)
            out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, form=diag)  # AIPOS-F204 件②
            out["category"] = "INGEST_TIP_MISMATCH"
            out["reasons"] = [
                f"分支 {branch} tip={tip} ≠ Return 自述 commit_sha={commit_sha}。形式诊断: {diag['text']}。"
                f"出口: 执行体以分支 tip 更新 Return frontmatter(交回的必须是当前产物)"
            ]
            return out
        tree = _git_out(code_repo, "rev-parse", f"{commit_sha}^{{tree}}")
        if tree != tree_hash:
            if tree:  # AIPOS-F204 件②: tree_hash 写错 = 报告头可纠正(应为该 commit 的 tree)
                out["header_problem"] = return_header_problem(workspace_root, task_id, card_fm, fm, mismatched={"tree_hash": (tree_hash, tree)})
            out["category"] = "INGEST_TREE_MISMATCH"
            out["reasons"] = [f"{commit_sha[:12]} tree={str(tree)[:12]} ≠ Return 自述 tree_hash={tree_hash[:12]}"]
            return out
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
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
    refusal = slot_code_refusal(verdict_artifact_dir(workspace_root, task_id))  # AIPOS-F195 件①: 审计落点同理
    if refusal is not None:
        out["category"], out["reasons"], out["code_files"] = refusal["category"], refusal["reasons"], refusal["code_files"]
        return out
    fm = _read_frontmatter(path, allow_missing_block=True)
    out["frontmatter"] = fm
    # AIPOS-F89 件③c: 报告完成判据唯一实现 next_resolver.missing_verdict_frontmatter(占位 = 未填, 空模板不算产物)
    from tools.aipos_cli.next_resolver import card_branch_name, missing_verdict_frontmatter

    missing = missing_verdict_frontmatter(fm, governance=governance)
    if missing:
        where = "verdict.governance_root.required_frontmatter" if governance else "verdict.required_frontmatter"
        out["category"] = "INGEST_FRONTMATTER_MISSING"
        out["reasons"] = [f"{path}: frontmatter 缺 {', '.join(missing)}(声明: transitions.schema artifact_ingest.{where})"]
        return out
    reviewed = str(fm.get("reviewed_task_id") or "").strip() or audit_card_reviewed_id(task_id, card_fm)
    if governance:
        # AIPOS-F197 件②: 被审卡只写治理根 = 报告 outputs_digest 须 = 交回绑定指纹 = 此刻指纹(governance_outputs_recheck 唯一实现)
        from tools.aipos_cli.next_resolver import governance_outputs_recheck

        recheck = governance_outputs_recheck(workspace_root, reviewed, repo_fm)
        reported = str(fm.get("outputs_digest") or "").strip()
        if not recheck["ok"] or reported != str((recheck.get("bound") or {}).get("outputs_digest") or ""):
            out["category"] = recheck["code"] if not recheck["ok"] else "INGEST_OUTPUTS_CHANGED"
            out["reasons"] = [recheck["reason"] if not recheck["ok"] else
                              f"{path}: 报告 outputs_digest={reported!r} ≠ 被审卡 {reviewed} 交回绑定指纹 "
                              f"{recheck['bound']['outputs_digest']}(审的必须是交回的那份产出); 出口: 报告照抄开工提示里产品给出的值"]
            return out
        from tools.aipos_cli.board_adapter import runtime_evidence_check

        evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
        if evidence is not None:
            out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
            return out
        out["outputs_manifest"] = recheck["current"]
        out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
        return out
    # AIPOS-F196 件①: 被审卡交付主体为固定候选(validation)= 报告 commit_sha 核候选, 不核被审卡分支 tip
    candidate_check = _candidate_subject_check(workspace_root, reviewed, repo_fm, fm, path, kind="verdict")
    if candidate_check is not None:
        if candidate_check["ok"]:
            from tools.aipos_cli.board_adapter import runtime_evidence_check

            evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
            if evidence is not None:
                out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
                return out
        out.update(candidate_check)
        return out
    branch = card_branch_name(reviewed)  # AIPOS-F108 件②: 唯一读取口(声明缺 = SchemaLoadError, 不回落写死)
    tip = _git_out(code_repo, "rev-parse", "--verify", f"{branch}^{{commit}}")
    commit_sha = str(fm.get("commit_sha")).strip()
    if tip is None:
        out["category"], out["reasons"] = "INGEST_BRANCH_MISSING", [f"{code_repo}: 被审卡分支 {branch} 不存在"]
        return out
    if tip != commit_sha:
        # AIPOS-F180B item 1: full values on both sides + form diagnosis; correctable = report header only (loop resumes the
        # same audit session, verbs.schema lybra_loop.launch.report_fix); other_commit keeps the F114 round outlet
        diag = out["tip_diagnosis"] = tip_mismatch_diagnosis(code_repo, tip, commit_sha, branch=branch)
        head = f"被审分支 {branch} tip={tip} ≠ 报告自述 commit_sha={commit_sha}。形式诊断: {diag['text']}"
        out["category"] = "INGEST_TIP_MISMATCH"
        out["reasons"] = [f"{head}。出口: {diag['outlet']}" if diag["correctable"]
                          else f"{head}: {diag['outlet']}" + _stale_round_outlet(workspace_root, task_id, card_fm, reviewed)]
        return out
    # AIPOS-F162 件① / AIPOS-F163: 运行证据硬门的判据在门侧裁决写入唯一口(board_adapter.runtime_evidence_check, 门
    # _build_audit_verdict_preview 对一切裁决提交路径同核); 此处调用同一判据预检, 拒收即不提交(拒因码同门侧)
    from tools.aipos_cli.board_adapter import runtime_evidence_check

    evidence = runtime_evidence_check(workspace_root, task_id, card_fm, report_path=path, report_fm=fm)
    if evidence is not None:
        out["category"], out["reasons"] = evidence["category"], evidence["reasons"]
        return out
    out["ok"], out["category"], out["exit_code"] = True, "OK", INGEST_EXIT_RECORDED
    return out


def _candidate_subject_check(workspace_root: Path, subject_task_id: str, subject_fm: dict[str, Any] | None, report_fm: dict[str, Any],
                             path: Path, *, kind: str) -> dict[str, Any] | None:
    """AIPOS-F196 件①: 交付主体为固定候选的卡(enums.schema task_mode.candidate_subject)的报告核对(return / verdict 同一实现)。
    None = 被核卡非 candidate 模式(调用方走卡分支 tip 判据); 否则 {ok, category, reasons[, exit_code]}:
    候选声明不合 = INGEST_CANDIDATE_INVALID; 报告三键(return: commit_sha / tree_hash / branch; verdict: commit_sha)≠ 产品给值 = INGEST_CANDIDATE_MISMATCH。"""
    from tools.aipos_cli.next_resolver import CandidateSubjectError, candidate_subject_values

    try:
        values = candidate_subject_values(workspace_root, subject_task_id, subject_fm or {})
    except CandidateSubjectError as exc:
        return {"ok": False, "category": "INGEST_CANDIDATE_INVALID",
                "reasons": [f"卡 {subject_task_id} 固定候选不可用: {exc}。出口: 顾问 lybra queue amend 修正来源卡 / 候选(enums.schema task_mode.candidate_subject)"]}
    if values is None:
        return None
    keys = ("commit_sha", "tree_hash", "branch") if kind == "return" else ("commit_sha",)
    mismatched = {k: (str(report_fm.get(k) or "").strip(), values[k]) for k in keys if str(report_fm.get(k) or "").strip() != values[k]}
    wrong = [f"{k}={got!r} ≠ 产品给出 {want}" for k, (got, want) in mismatched.items()]
    if wrong:
        return {"ok": False, "category": "INGEST_CANDIDATE_MISMATCH", "mismatched": mismatched,
                "reasons": [f"{path}: 本卡交付主体 = 来源卡 {values['source']} 的固定候选 {values['commit_sha']}(验证卡不建卡分支): "
                            + "; ".join(wrong) + "。出口: 报告 frontmatter 照抄开工提示里产品给出的值"]}
    return {"ok": True, "category": "OK", "reasons": [], "exit_code": INGEST_EXIT_RECORDED}


def header_fix_declaration() -> dict[str, Any]:
    """AIPOS-F204: transitions.schema artifact_ingest.return.header_fix(唯一读取口)。缺 / 形不合 = SchemaLoadError(fail-closed)。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("return") or {}).get("header_fix")
    kinds = decl.get("correctable_kinds") if isinstance(decl, dict) else None
    misuse = decl.get("contract_misuse") if isinstance(decl, dict) else None
    if (not isinstance(kinds, list) or not kinds or not isinstance(misuse, dict)
            or any(not isinstance(misuse.get(k), str) or not misuse[k].strip() for k in ("candidate", "branch", "governance"))):
        raise SchemaLoadError("transitions.schema.json artifact_ingest.return.header_fix(correctable_kinds / contract_misuse.candidate|branch|"
                              "governance)未声明齐(AIPOS-F204)")
    return decl


def return_header_problem(workspace_root: Path, task_id: str, card_fm: dict[str, Any], return_fm: dict[str, Any], *,
                          missing: list[str] | None = None, mismatched: dict[str, tuple[str, str]] | None = None,
                          form: dict[str, Any] | None = None) -> dict[str, Any]:
    """AIPOS-F204 件①②(声明 transitions artifact_ingest.return.header_fix): 执行卡交回报告头错误的唯一判定与补法渲染。

    调用方给出已判定的错处(missing = 缺 / 占位的必填键; mismatched = {键: (报告值, 应为值)}; form = 卡分支 tip_mismatch_diagnosis 结果);
    契约分形与逐键说明 = 本卡开工提示同一契约(next_resolver.card_report_contract), 另判误用另一契约的键(contract_misuse)。
    返回 {contract, kinds, missing, mismatched, misused, correctable, problem, fix, note, keys}:
      fix = 需改键逐键 `键 = 产品给值(说明)`(产品无值 = `键 = 说明`); note = 误用另一契约的说明句(无 = 空);
      problem = 判定原文(拒因 / 修头指令同读)。候选声明不合 = contract=invalid、correctable=False(出口照旧 amend)。"""
    from tools.aipos_cli.next_resolver import (
        CandidateSubjectError, _is_placeholder_value, candidate_subject_values, card_branch_name, card_governance_outputs,
        card_report_contract, required_return_frontmatter,
    )

    decl = header_fix_declaration()
    fm = {**(card_fm or {}), "task_id": str((card_fm or {}).get("task_id") or task_id)}
    missing = [str(k) for k in (missing or [])]
    mismatched = dict(mismatched or {})
    out: dict[str, Any] = {"contract": "", "kinds": [], "missing": missing, "mismatched": {k: list(v) for k, v in mismatched.items()},
                           "misused": [], "correctable": False, "problem": "", "fix": "", "note": "", "keys": []}
    try:
        candidate = candidate_subject_values(Path(workspace_root), task_id, fm)
        governance = candidate is None and card_governance_outputs(Path(workspace_root), fm)
        entries = card_report_contract(Path(workspace_root), task_id, fm)
    except CandidateSubjectError as exc:
        out["contract"] = "invalid"
        out["problem"] = f"卡 {task_id} 固定候选不可用({exc}), 产品给不出交回三键"
        out["fix"] = "顾问 lybra queue amend 修正来源卡 / 候选(enums.schema task_mode.candidate_subject)"
        return out
    contract = "candidate" if candidate is not None else "governance" if governance else "branch"
    out["contract"] = contract
    keys = [str(e["key"]) for e in entries]
    out["keys"] = keys
    other_keys = required_return_frontmatter(governance=not governance)
    misused = [k for k in other_keys if k not in keys and not _is_placeholder_value((return_fm or {}).get(k))]
    out["misused"] = misused
    expected = {str(e["key"]): e.get("value") for e in entries}
    hints = {str(e["key"]): str(e["hint"]) for e in entries}
    kinds: list[str] = []
    parts: list[str] = []
    need: list[str] = []
    if missing:
        kinds.append("missing")
        parts.append(f"缺 {', '.join(missing)}")
        need += missing
    if mismatched:
        kinds.append("mismatch")
        parts.append("; ".join(f"{k}={got!r} ≠ 应为 {want}" for k, (got, want) in mismatched.items()))
        for key, (_got, want) in mismatched.items():
            expected[key] = want
            need.append(key)
    if form:
        kinds.append("form")
        parts.append(f"commit_sha={form.get('commit_sha')} ≠ 卡分支 {form.get('branch')} tip={form.get('tip')}(形式诊断: {form.get('text')})")
        expected["commit_sha"] = str(form.get("tip") or "") or expected.get("commit_sha")
        need.append("commit_sha")
    if misused:
        kinds.append("contract_misuse")
        what = {"candidate": candidate or {}, "branch": {"branch": card_branch_name(task_id)}}.get(contract, {})
        note = str(decl["contract_misuse"][contract])
        for key, value in (("source", what.get("source", "")), ("branch", what.get("branch", "")), ("keys", " / ".join(keys)),
                           ("misused", " / ".join(misused))):
            note = note.replace("{" + key + "}", str(value))
        out["note"] = note
        parts.append(note)
    out["kinds"] = kinds
    ordered = [k for k in keys if k in need] + [k for k in need if k not in keys]
    out["fix"] = "; ".join(f"{k} = {expected[k]}({hints[k]})" if expected.get(k) and k in hints else
                           f"{k} = {expected[k]}" if expected.get(k) else f"{k} = {hints.get(k, '按声明填写实值')}"
                           for k in dict.fromkeys(ordered))
    out["problem"] = "; ".join(p for p in parts if p)
    allowed = {str(k) for k in decl["correctable_kinds"]}
    out["correctable"] = bool(kinds) and all(k in allowed for k in kinds) and (form is None or bool(form.get("correctable")))
    return out


# ---------------------------------------------------------------------------
# AIPOS-F162 件①: 运行证据硬门——声明 transitions.schema artifact_ingest.verdict.runtime_evidence(唯一);
# 本节 = 声明读取 / 卡面声明解析 / 证据节解析 / 要求段渲染。判据本身(AIPOS-F163 下沉)在门侧裁决写入唯一口
# board_adapter.runtime_evidence_check(门 _build_audit_verdict_preview、本模块 _validate_task_artifact 预检、推导核 next_resolver 同读)
# ---------------------------------------------------------------------------

class RuntimeEvidenceDeclarationError(ValueError):
    """卡面 audit_runtime_evidence 形不对(fail-closed: 不当未声明放行)。"""


def runtime_evidence_declaration() -> dict[str, Any]:
    """transitions artifact_ingest.verdict.runtime_evidence 唯一读取口。缺 / 关键键缺 = SchemaLoadError。"""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = (_artifact_ingest_declaration().get("verdict") or {}).get("runtime_evidence")
    needed = ("card_field", "applies_to_verdicts", "section_heading", "block_language", "entry_required_fields", "log_fields",
              "placeholders", "rejection_category", "declaration_invalid_category", "loop")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in needed):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence({'/'.join(needed)})未声明")
    return decl


def runtime_evidence_requirement(audit_fm: dict[str, Any] | None, reviewed_fm: dict[str, Any] | None) -> dict[str, Any] | None:
    """卡面运行证据要求: 审计卡自身声明优先(补充审计写入), 缺省读被审卡。返回 {commands, source} 或 None(未声明 / required 非 true)。
    形不对 = RuntimeEvidenceDeclarationError。draft 校验与裁决入口同用本函数解析。"""
    key = str(runtime_evidence_declaration()["card_field"])
    raw, source = None, ""
    for fm, where in ((audit_fm, "audit_card"), (reviewed_fm, "reviewed_card")):
        if isinstance(fm, dict) and fm.get(key) not in (None, ""):
            raw, source = fm.get(key), where
            break
    if raw is None:
        return None
    return parse_runtime_evidence_declaration(raw, source=source)


def parse_runtime_evidence_declaration(raw: Any, *, source: str = "card") -> dict[str, Any] | None:
    key = str(runtime_evidence_declaration()["card_field"])
    if not isinstance(raw, dict):
        raise RuntimeEvidenceDeclarationError(f"{key} 须为对象 {{required: true|false, commands: [...]}}, 实为 {type(raw).__name__}")
    unknown = sorted(set(raw) - {"required", "commands"})
    if unknown:
        raise RuntimeEvidenceDeclarationError(f"{key} 含未声明的键 {unknown}(只认 required / commands)")
    required = raw.get("required")
    if not isinstance(required, bool):
        raise RuntimeEvidenceDeclarationError(f"{key}.required 须为布尔 true/false, 实为 {required!r}")
    commands = raw.get("commands", [])
    if commands is None:
        commands = []
    if not isinstance(commands, list) or any(not isinstance(c, str) or not c.strip() for c in commands):
        raise RuntimeEvidenceDeclarationError(f"{key}.commands 须为非空命令串列表, 实为 {commands!r}")
    if not required:
        return None
    return {"commands": [c.strip() for c in commands], "source": source}


# ---------------------------------------------------------------------------
# AIPOS-F165 件①: 派发前可执行核查——声明 transitions.schema artifact_ingest.verdict.runtime_evidence.executable_check;
# 声明校验唯一处 check_runtime_evidence_declaration(出卡 draft_validator 与补审派发 board_adapter 同调; 裁决入口判据不经此, 逐字匹配不变)
# ---------------------------------------------------------------------------

_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"})
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def runtime_evidence_command_words(command: str) -> list[str]:
    """声明命令中每段的可执行词(按 shell 控制符切段; 跳过前导 VAR=值; cd 段不计; env 跳过选项/赋值后取可执行)。
    shell 引号不配对 = RuntimeEvidenceDeclarationError(声明形不合, fail-closed)。"""
    import shlex

    check = runtime_evidence_declaration().get("executable_check") or {}
    with_arg = {str(o) for o in check.get("env_options_with_arg") or []}
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise RuntimeEvidenceDeclarationError(f"命令 {command!r} 不可按 shell 词法解析: {exc}") from exc
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SHELL_SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    words: list[str] = []
    for segment in segments:
        rest = list(segment)
        while rest and _ASSIGNMENT_RE.match(rest[0]):
            rest.pop(0)
        if not rest or rest[0] == "cd":
            continue
        if rest[0] == "env" or rest[0].endswith("/env"):
            words.append(rest[0])
            rest.pop(0)
            while rest and (rest[0].startswith("-") or _ASSIGNMENT_RE.match(rest[0])):
                option = rest.pop(0)
                if option == "--":
                    break
                if option in with_arg and rest:
                    rest.pop(0)
            while rest and _ASSIGNMENT_RE.match(rest[0]):
                rest.pop(0)
            if not rest:
                continue
        words.append(rest[0])
    return words


def check_runtime_evidence_declaration(raw: Any, *, governance_root: Path | str, audit_instance: str | None,
                                       card_task_id: str | None = None, card_frontmatter: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行证据声明校验唯一处(出卡 draft create/publish 与补审派发 audit dispatch --supplementary 同调): 形校验(同裁决入口
    parse_runtime_evidence_declaration)+ 派发前可执行核查。返回 {requirement, problems: [拒因原文], notes: [未核提示]}。
    形不合 = RuntimeEvidenceDeclarationError。problems 非空 = 调用方拒(预演报错, --confirm 拒)。
    AIPOS-F199 件①: card_frontmatter(声明所在卡)交付主体为固定候选且命令用旧占位 = declaration_invalid_category 拒因 + 迁移提示
    (runtime_evidence_legacy_problems 唯一判据; 裁决入口同判); 候选声明本身不合由起草 F196 判据报, 此处不重复。"""
    import socket

    requirement = parse_runtime_evidence_declaration(raw)
    out: dict[str, Any] = {"requirement": requirement, "problems": [], "notes": []}
    if requirement is None:
        return out
    decl = runtime_evidence_declaration()
    if card_frontmatter is not None:
        from tools.aipos_cli.next_resolver import CandidateSubjectError

        try:
            legacy = runtime_evidence_legacy_problems(requirement, str(card_task_id or card_frontmatter.get("task_id") or ""), card_frontmatter)
        except CandidateSubjectError:  # 候选声明不合: 起草 F196 判据(VALIDATION_*)已拒并给出口, 此处不二报
            legacy = []
        out["problems"].extend(f"{decl['declaration_invalid_category']}: {p}" for p in legacy)
    check = decl.get("executable_check")
    needed = ("category", "privilege_category", "privilege_commands", "fix_hint", "unverified_note")
    if not isinstance(check, dict) or any(not check.get(k) for k in needed):
        from tools.schema_loader import SchemaLoadError

        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence.executable_check({'/'.join(needed)})未声明")
    placeholders = ["{" + str(k) + "}" for k in (*decl["placeholders"], *runtime_evidence_candidate_declaration()["placeholders"])]
    privileged = {str(c) for c in check["privilege_commands"]}
    location: dict[str, Any] = {}
    basis = ""
    if audit_instance:
        from tools.aipos_cli.enrollment import workstation_location
        from tools.schema_loader import SchemaLoadError

        try:
            location = workstation_location(governance_root, str(audit_instance))
        except (OSError, ValueError, SchemaLoadError) as exc:
            basis = f"审计工位 {audit_instance} 位置读取失败({exc}), 按治理根所在机核"
    remote = bool(location.get("found")) and location.get("transport") == "remote"
    if not basis:
        basis = (f"审计工位 {audit_instance} 在本机" if location.get("found") else
                 f"审计工位{(' ' + str(audit_instance) + ' ') if audit_instance else ''}位置定位不到"
                 f"({location.get('reason') or '无审计实例'}), 按治理根所在机核")
    host = socket.gethostname()
    unverified: list[str] = []
    for index, command in enumerate(requirement["commands"], 1):
        for word in runtime_evidence_command_words(command):
            name = word.rsplit("/", 1)[-1]
            if word in privileged or name in privileged:
                out["problems"].append(f"{check['privilege_category']}: 命令 #{index} `{command}` 含提权命令 `{word}`; 运行证据命令须以审计体自身权限"
                                       f"可执行, 不得要求提权")
                continue
            if not word.startswith("/") or any(p in word for p in placeholders):
                continue  # PATH 名 / 相对路径 / 含占位 = 审计体按其 PATH 与工作树解析, 派发时不核
            if remote:
                unverified.append(word)
                continue
            if not (os.path.isfile(word) and os.access(word, os.X_OK)):
                out["problems"].append(
                    f"{check['category']}: 命令 #{index} `{command}` 中的可执行 `{word}` 在审计工位主机(本机 {host}; {basis})不存在或不可执行; "
                    + str(check["fix_hint"]).replace("{name}", name))
    if unverified:
        out["notes"].append(str(check["unverified_note"]).replace("{instance}", str(audit_instance))
                            .replace("{host}", str(location.get("host"))).replace("{paths}", ", ".join(dict.fromkeys(unverified))))
    return out


def runtime_evidence_guidance() -> str:
    """AIPOS-F165 件②: 运行证据拒因 / 要求段的固定指引。文案唯一来源 = roles.schema write_boundary.hard_rules 中
    id = transitions runtime_evidence.rejection_guidance.guidance_rule_id 的条文(章程「写权限边界」节同源下发)。缺 = SchemaLoadError。"""
    from tools.aipos_cli.write_boundary import load_write_boundary_declaration
    from tools.schema_loader import SchemaLoadError

    rule_id = str((runtime_evidence_declaration().get("rejection_guidance") or {}).get("guidance_rule_id") or "")
    rule = next((r for r in load_write_boundary_declaration()["hard_rules"] if isinstance(r, dict) and r.get("id") == rule_id), None)
    if not rule_id or rule is None or not str(rule.get("text") or "").strip():
        raise SchemaLoadError(f"roles.schema.json write_boundary.hard_rules 无 id={rule_id!r} 的条文"
                              "(transitions runtime_evidence.rejection_guidance.guidance_rule_id)")
    return f"固定指引(硬条款 {rule_id}): {str(rule['text']).strip()}"


def runtime_evidence_command_diff(declared: str, ran: list[str]) -> str:
    """AIPOS-F165 件②: 一条缺运行记录的声明命令(已渲染)与最接近的实跑命令逐词对比 → 「声明 vs 实跑」差异原文。
    最接近 = difflib 相似度最高的一条; 无实跑记录 = 只说明无可对比。只作拒因说明, 判据(逐字匹配)不变。"""
    import difflib

    if not ran:
        return "逐条差异: 报告无任何实跑命令可对比"
    closest = max(ran, key=lambda c: difflib.SequenceMatcher(None, declared, c).ratio())
    want, got = declared.split(), closest.split()
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, want, got).get_opcodes():
        if tag != "equal":
            parts.append(f"声明 `{' '.join(want[i1:i2]) or '(无)'}` ≠ 实跑 `{' '.join(got[j1:j2]) or '(无)'}`")
    return f"逐条差异: 声明 `{declared}` vs 最接近的实跑 `{closest}`: " + ("; ".join(parts) or "仅空白不同")


def runtime_evidence_candidate_declaration() -> dict[str, Any]:
    """AIPOS-F199 件①: transitions runtime_evidence.candidate_subject(固定候选卡审计证据绑定审计自己的验证根 run)唯一读取口。缺 = SchemaLoadError。"""
    from tools.schema_loader import SchemaLoadError

    decl = runtime_evidence_declaration().get("candidate_subject")
    needed = ("entry_required_fields", "placeholders", "legacy_placeholders", "legacy_hint", "outlet", "steps_title", "steps", "command_line",
              "pending", "remote_steps")
    if not isinstance(decl, dict) or any(not decl.get(k) for k in needed):
        raise SchemaLoadError(f"transitions.schema.json artifact_ingest.verdict.runtime_evidence.candidate_subject({'/'.join(needed)})未声明")
    return decl


def render_candidate_evidence_command(command: str, values: dict[str, Any]) -> str:
    """AIPOS-F199: 固定候选卡声明命令的占位渲染(唯一实现; 裁决入口逐字匹配与开工稿 / 派审步骤同用)。占位名读 candidate_subject.placeholders;
    values 缺某占位 = 原样保留(开工稿未起 run 时)。"""
    for name in runtime_evidence_candidate_declaration()["placeholders"]:
        if name in values and values[name] not in (None, ""):
            command = command.replace("{" + str(name) + "}", str(values[name]))
    return command


def runtime_evidence_legacy_problems(requirement: dict[str, Any] | None, card_task_id: str, card_fm: dict[str, Any] | None) -> list[str]:
    """AIPOS-F199 件①: 固定候选卡(next_resolver.card_candidate_subject)的声明命令仍用旧占位({worktree}/{commit_sha}/{tree_hash})= 迁移提示原文
    (每条一句; 非候选卡 / 无要求 = [])。候选声明本身不合 = CandidateSubjectError 上抛(调用方各自给拒因)。"""
    from tools.aipos_cli.next_resolver import card_candidate_subject

    if not requirement or card_candidate_subject(card_fm) is None:
        return []
    decl = runtime_evidence_candidate_declaration()
    out: list[str] = []
    for command in requirement.get("commands") or []:
        legacy = [f"{{{name}}}" for name in decl["legacy_placeholders"] if "{" + str(name) + "}" in command]
        if legacy:
            out.append(str(decl["legacy_hint"]).format(task_id=card_task_id, task_mode=(card_fm or {}).get("task_mode"),
                                                       card_field=runtime_evidence_declaration()["card_field"], command=command,
                                                       legacy=" / ".join(legacy)))
    return out


def render_candidate_evidence_steps(governance_root: Path | str, *, audit_task_id: str, audit_instance: str | None, reviewed_task_id: str,
                                    reviewed_fm: dict[str, Any] | None, requirement: dict[str, Any] | None) -> list[str]:
    """AIPOS-F199 件②: 审计「验证卡运行证据步骤」(派审注入 / 开工稿同一渲染; 步骤措辞读 candidate_subject.steps, 渲染值与命令读
    validation_root.candidate_evidence_values 唯一铸造口)。被审卡非固定候选 = []。候选不可用 = CandidateSubjectError 上抛。"""
    from tools.aipos_cli.next_resolver import candidate_subject_values
    from tools.aipos_cli.validation_root import candidate_evidence_values

    subject = candidate_subject_values(Path(governance_root), reviewed_task_id, reviewed_fm or {})
    if subject is None:
        return []
    decl = runtime_evidence_candidate_declaration()
    values = candidate_evidence_values(governance_root, audit_task_id=audit_task_id, audit_instance=str(audit_instance or ""),
                                       candidate=subject["commit_sha"], candidate_tree=subject["tree_hash"])
    values["heading"] = str(runtime_evidence_declaration()["section_heading"])
    known = {k: v for k, v in values.items() if not str(v).startswith("<")}
    lines = [str(decl["steps_title"])]
    after = decl.get("commands_after_step")
    for index, step in enumerate(decl["steps"], 1):
        lines.append(str(step).format(**values))
        if index == after:  # 声明命令逐条(占位按已知值渲染), 位置读声明 commands_after_step
            lines.extend(str(decl["command_line"]).format(command=render_candidate_evidence_command(c, known))
                         for c in (requirement or {}).get("commands") or [])
    if values.get("remote"):  # AIPOS-F200 件④: 审计 run 在远端主机 = 证据清单 + 取回副本两步(声明 candidate_subject.remote_steps)
        lines.extend(str(step).format(**values) for step in decl.get("remote_steps") or [])
    lines.extend(f"   ⚠ {p}" for p in runtime_evidence_legacy_problems(requirement, reviewed_task_id, reviewed_fm))
    return lines


def render_runtime_evidence_requirement(requirement: dict[str, Any], *, candidate_steps: list[str] | None = None) -> str:
    """审计卡正文「独立运行证据要求」段(唯一渲染; 派生审计卡与手动/补充派审同读): 命令清单 + 证据节格式 + 示例。
    AIPOS-F199 件②: candidate_steps(被审卡交付主体为固定候选, render_candidate_evidence_steps)= 证据绑定审计自己的验证根 run, 不提审计分配工作树。"""
    decl = runtime_evidence_declaration()
    keys = decl["log_fields"]
    heading = str(decl["section_heading"])
    if candidate_steps:
        cand = runtime_evidence_candidate_declaration()
        lines = ["", "## 独立运行证据要求(AIPOS-F162: 裁决入口硬门; AIPOS-F199: 固定候选卡绑定审计自己的验证根 run)", "",
                 f"- 本卡声明 {decl['card_field']}.required=true: PASS / PASS_WITH_NOTES 报告必须含 `## {heading}` 节, 否则裁决入口拒收"
                 f"({decl['rejection_category']}), loop 让你补做",
                 "- 独立核验 = 在**你自己的验证根 run**(审计卡声明的 run, 不是执行实例的 run)重跑下列命令; 只核执行体原件 hash/退出码不算; "
                 "跑不了(环境/时长)给 FAIL 或 BLOCK 并写原因",
                 f"- 命令占位: {', '.join('{' + str(k) + '}' for k in cand['placeholders'])}(本 run 目录 / 本 run 送达的候选源码 / 固定候选 / 候选 tree), "
                 "记录里写渲染后的实际命令, 逐字一致",
                 f"- {runtime_evidence_guidance()}", "", *candidate_steps, "",
                 f"证据节格式(节内第一个 ```{decl['block_language']} 块; 每条必填 {', '.join(cand['entry_required_fields'])}):", "",
                 f"```{decl['block_language']}",
                 '[{"command": "<实际命令>", "run_id": "<run-id>", "run_dir": "<本 run 目录绝对路径>", "exit_code": 0, '
                 f'"{keys["path"]}": "<日志绝对路径>", "{keys["sha256"]}": "<sha256>"' + "}]",
                 "```", ""]
        return "\n".join(lines)
    lines = ["", "## 独立运行证据要求(AIPOS-F162: 裁决入口硬门)", "",
             f"- 本卡声明 {decl['card_field']}.required=true: PASS / PASS_WITH_NOTES 报告必须含 `## {heading}` 节, 否则裁决入口拒收"
             f"({decl['rejection_category']}), loop 让你补做",
             "- 独立核验 = 在**你自己的审计分配工作树**重跑下列命令; 只核执行体原件 hash/退出码不算; 跑不了(环境/时长)给 FAIL 或 BLOCK 并写原因",
             f"- 命令占位: {', '.join('{' + str(k) + '}' for k in decl['placeholders'])}(审计分配工作树 / 被审 tip / tree), 记录里写渲染后的实际命令, 逐字一致",
             f"- {runtime_evidence_guidance()}"]  # AIPOS-F165 件②: 与拒因同一固定指引
    if requirement.get("commands"):
        lines.append("- 必须逐条运行并记录:")
        lines.extend(f"  - `{c}`" for c in requirement["commands"])
    else:
        lines.append("- 卡面未列具体命令: 记录你实际运行的验证命令(至少一条)")
    lines += ["", f"证据节格式(节内第一个 ```{decl['block_language']} 块; 日志二选一: {keys['excerpt']} 或 {keys['path']}+{keys['sha256']}):", "",
              f"```{decl['block_language']}",
              '[{"command": "<实际命令>", "worktree": "<审计分配工作树绝对路径>", "exit_code": 0, '
              f'"{keys["path"]}": "<日志绝对路径>", "{keys["sha256"]}": "<sha256>"' + "}]",
              "```", ""]
    return "\n".join(lines)


def parse_runtime_evidence_section(report_text: str) -> tuple[list[Any] | None, str]:
    """报告「独立运行证据」节内首个声明语言代码块 → (运行记录列表, 问题)。节缺 / 块缺 / JSON 坏 / 非列表 = (None, 问题原文)。"""
    import json as _json

    decl = runtime_evidence_declaration()
    heading = str(decl["section_heading"])
    fence = "```" + str(decl["block_language"])
    lines = report_text.splitlines()
    start = None
    level = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip() == heading:
            start, level = i + 1, len(stripped) - len(stripped.lstrip("#"))
            break
    if start is None:
        return None, f"报告缺「{heading}」节(标题行 `{'#' * 2} {heading}`)"
    body: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) - len(stripped.lstrip("#")) <= level and stripped.lstrip("#").startswith(" "):
            break
        body.append(line)
    block: list[str] | None = None
    for line in body:
        stripped = line.strip()
        if block is None:
            if stripped == fence:
                block = []
            continue
        if stripped == "```":
            break
        block.append(line)
    else:
        if block is not None:
            return None, f"「{heading}」节的 {fence} 代码块未闭合"
    if block is None:
        return None, f"「{heading}」节内无 {fence} 代码块(运行记录须机器可解析)"
    try:
        entries = _json.loads("\n".join(block))
    except ValueError as exc:
        return None, f"「{heading}」节 {fence} 代码块不是合法 JSON: {exc}"
    if not isinstance(entries, list):
        return None, f"「{heading}」节 {fence} 代码块须为运行记录数组, 实为 {type(entries).__name__}"
    return entries, ""


_TIP_DIAGNOSIS_KINDS = ("length_invalid", "non_hex", "prefix_miscopy", "not_a_commit", "other_commit")


def tip_mismatch_declaration() -> dict[str, Any]:
    """AIPOS-F180B item 1: transitions.schema artifact_ingest.tip_mismatch_diagnosis (single reader). Missing / malformed = SchemaLoadError."""
    from tools.aipos_cli.next_resolver import _artifact_ingest_declaration
    from tools.schema_loader import SchemaLoadError

    decl = _artifact_ingest_declaration().get("tip_mismatch_diagnosis")
    where = "transitions.schema.json artifact_ingest.tip_mismatch_diagnosis"
    if not isinstance(decl, dict) or not isinstance(decl.get("kinds"), dict) or not isinstance(decl.get("outlets"), dict):
        raise SchemaLoadError(f"{where} (kinds / outlets) not declared")
    for key in ("prefix_min_chars", "min_resolve_chars"):
        if not isinstance(decl.get(key), int) or isinstance(decl.get(key), bool) or decl[key] < 1:
            raise SchemaLoadError(f"{where}.{key} must be a positive integer")
    for kind in _TIP_DIAGNOSIS_KINDS:
        spec = decl["kinds"].get(kind)
        if not isinstance(spec, dict) or not isinstance(spec.get("correctable"), bool) or not str(spec.get("text") or "").strip():
            raise SchemaLoadError(f"{where}.kinds.{kind} (correctable bool / text) not declared")
    if any(not str(decl["outlets"].get(k) or "").strip() for k in ("correctable", "not_correctable")):
        raise SchemaLoadError(f"{where}.outlets (correctable / not_correctable) not declared")
    return decl


def tip_mismatch_diagnosis(repo: Path, tip: str, commit_sha: str, *, branch: str = "") -> dict[str, Any]:
    """AIPOS-F180B item 1: form diagnosis of a self-reported commit_sha that differs from the branch tip (single implementation;
    judged only where INGEST_TIP_MISMATCH is decided). Order per declaration: resolves to another commit = other_commit (the only
    non-correctable kind); otherwise accumulate length_invalid / non_hex / prefix_miscopy, none of them = not_a_commit.
    Returns {branch, tip, commit_sha, kinds, correctable, resolved, text, outlet}."""
    decl = tip_mismatch_declaration()
    kinds_decl = decl["kinds"]
    sha = str(commit_sha or "")
    resolved = None
    if len(sha) >= int(decl["min_resolve_chars"]) and re.fullmatch(r"[0-9a-fA-F]+", sha):
        resolved = _git_out(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    hits: list[tuple[str, dict[str, Any]]] = []
    if resolved and resolved != tip:
        hits.append(("other_commit", {"resolved": resolved}))
    else:
        if len(sha) != 40:
            hits.append(("length_invalid", {"length": len(sha)}))
        bad = "".join(sorted(set(re.sub(r"[0-9a-f]", "", sha))))
        if bad:
            hits.append(("non_hex", {"chars": bad}))
        common = len(os.path.commonprefix([sha, str(tip)]))
        if common >= int(decl["prefix_min_chars"]):
            hits.append(("prefix_miscopy", {"common": common, "diverge": common + 1}))
        if not hits:
            hits.append(("not_a_commit", {}))
    texts = []
    for kind, values in hits:
        text = str(kinds_decl[kind]["text"])
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        texts.append(text)
    correctable = all(bool(kinds_decl[kind]["correctable"]) for kind, _v in hits)
    return {"branch": branch, "tip": tip, "commit_sha": sha, "kinds": [k for k, _v in hits], "correctable": correctable, "resolved": resolved,
            "text": "; ".join(texts), "outlet": str(decl["outlets"]["correctable" if correctable else "not_correctable"])}


def _stale_round_outlet(workspace_root: Path, audit_task_id: str, card_fm: dict[str, Any], reviewed: str) -> str:
    """AIPOS-F114 件②: 裁决入口遇旧 tip 的拒因出口(推导核同一判据: audit_derivation.superseding_round / next_resolver.return_staleness)。
    本轮已被取代 → 点名取代它的一轮; 所审交回已过期 → 驱动方重交回命令(产品随后派下一轮, 本轮作废); 其余 → 审计体按当前 tip 重审。"""
    from tools.aipos_cli.audit_derivation import superseding_round
    from tools.aipos_cli.next_resolver import (
        _find_connection_json, _find_task_in_queue, _read_frontmatter, ingest_command, return_staleness,
    )

    successor = superseding_round(audit_task_id, workspace_root, card_fm)
    if successor is not None:
        return (f"。出口: 本轮 {audit_task_id} 已被 {successor['audit_task_id']} 取代(派审记录 {successor['dispatch_id']} supersedes), "
                f"本报告不入门; 审计在 {successor['audit_task_id']} 对当前 tip 进行")
    reviewed_path, _q = _find_task_in_queue(workspace_root, reviewed)
    stale = return_staleness(workspace_root, reviewed, _read_frontmatter(reviewed_path)) if reviewed_path else None
    if stale is not None and str(stale.get("audit_task_id") or "") == audit_task_id:
        command = ingest_command(reviewed, "return", workspace_root, _find_connection_json(workspace_root))
        return (f"。交回已过期({stale['reason']})。出口: 驱动方重交回被审卡 `{command}`"
                f"(执行体 Return 须已是卡分支当前 tip), 产品随后按卡号演进派下一轮审计, 本轮 {audit_task_id} 作废")
    return "。出口: 审计体对被审分支当前 tip 重审并更新报告 commit_sha"


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
    """AIPOS-F90 件②: 产品填写的模型字段(agent_runtime 形)。harness = 写报告那张卡的 harness(AIPOS-F211 件③: 唯一推导
    loop_driver.card_harness——卡面值, 缺 = 本卡执行方式推出, 与 loop 拉起同读; 原卡面缺按 task_mode 补 "code" 查表退役);
    执行者实例无子 agent 接入登记(CardHarnessUnresolved)= harness 不可知 → 模型 missing_value;
    定位器缺/会话记录里找不到写该报告的条目 = missing_value(「未声明会话记录」), 绝不采信自报; 自报只作对照, 不一致标 model_mismatch。"""
    from tools.aipos_cli.loop_driver import CardHarnessUnresolved, card_harness
    from tools.aipos_cli.next_resolver import _read_task_records

    decl = _session_locators()
    missing = str(decl["missing_value"]).strip()
    try:
        harness = card_harness(writer_fm, Path(workspace_root), task_id=writer_task_id)
    except CardHarnessUnresolved:
        harness = ""
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
        result["exit_code"] = INGEST_EXIT_REJECTED  # AIPOS-F101 件③: 原沿用校验通过的 0 = 拒却 exit 0(与声明不符)
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
        result.update({"ok": True, "exit_code": INGEST_EXIT_RECORDED, "category": "DRY_RUN", "message": "校验通过(未提交)"})
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
        result["exit_code"] = INGEST_EXIT_RECORDED if result["ok"] else INGEST_EXIT_SHELL_REJECTED
        result["output"] = str(written.get("path") or "")
        result["message"] = "finalization 记录已落(external finalize, 来自 FINALIZE Return)" if result["ok"] else "finalization 记录写入失败"
        result["category"] = "OK" if result["ok"] else "RECORD_WRITE_FAILED"
        return result
    runner = execute or execute_derived_action
    exec_result = runner({**derivation, "command": shell_command}, Path(workspace_root), connection_json)
    result["ok"] = bool(exec_result.get("ok"))
    result["exit_code"] = INGEST_EXIT_RECORDED if result["ok"] else int(exec_result.get("exit_code") or INGEST_EXIT_SHELL_REJECTED)
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
        governance_root = Path(_find_repo_root_for_args(args))  # AIPOS-F144: 唯一薄壳(全局/子命令级 --workspace-root 同义)
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
